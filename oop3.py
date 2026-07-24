from tkinter import Tk, Label, Button, StringVar
from tkinter.ttk import Progressbar, Combobox
from tkinter.filedialog import askopenfile
from tkinter.messagebox import showinfo, showerror
from threading import Thread
from time import sleep 
from contextlib import redirect_stderr
from demucs.pretrained import get_model
from demucs.apply import apply_model
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
import os
import subprocess
import queue
import re
import torch
import cv2
import soundfile as sf

# Constants
WINDOW_BG = '#222222'
BTN_BG = '#0E6CB4'
FG = 'white'
BTN_STYLE = {'bg': BTN_BG, 'fg': FG}
BTN_PACKING = {'ipadx': 20, 'ipady': 5}
SUPPORTED_VIDEO_FORMATS = '*.MP4 *.MKV *.MOV *.WEBM *.AVI *.FLV'

MODEL = get_model('htdemucs')
MODEL.eval()

def get_program_path(name):
    '''
    It returns the path of the given program `name`.
    '''
    if getattr(sys, 'frozen', False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).parent

    return str(base / name)

def get_existing_clips_only(clips):
    '''
    It returns only the existing clips; Preventing any FileNotFoundError.
    '''
    existing_clips = clips

    for clip in clips:
        if not os.path.exists(clip):
            existing_clips.remove(clip)
    
    return existing_clips

def get_video_length(video_file_path):
    '''
    It returns the given video's length.
    '''
    video = cv2.VideoCapture(video_file_path)
    fps = video.get(cv2.CAP_PROP_FPS)
    frame_count = int(video.get(cv2.CAP_PROP_FRAME_COUNT))
    video.release()

    return round(frame_count / fps)

def get_dimension_args(dimensions: str):
    '''
    It returns the appropriate `ffplay` args depending on the given `dimensions`
    '''
    if dimensions == 'وضع الشاشة الكاملة':
        return ['-fs']
    else:
        return [
            '-x', dimensions.split('x')[0],
            '-y', dimensions.split('x')[1]
        ]

def load_audio(path):
    '''
    It returns the waveform and the sample rate of the given\n
    audio file path.
    '''
    wav, sr = sf.read(path, dtype='float32')

    # Mono -> (samples, 1)
    if wav.ndim == 1:
        wav = wav[:, None]

    # (samples, channels) -> (channels, samples)
    wav = torch.from_numpy(wav.T)

    return wav, sr

def save_vocals(path, wav, sr):
    '''
    It saves the `vocals` stem at the temporary directory given with `path`.
    '''
    sf.write(
        path / 'vocals.wav',
        wav.detach().cpu().numpy().T,
        sr 
    )



class ProgressRecorder():
    '''
    If you want to customize redirect_stderr/redirect_stdout,\n
    you have to make a class with two methods: `write(text)`, `flush()`.\n
    `text` will contain the stderr/stdout; So you can deal with it as you want.
    '''
    def setup(self, app):
        self.app = app

    def write(self, text):
        match = re.search(r'(\d+)%', text) # search for a number followed by %.

        if match: 
            percentage = int(match.group(1))
            self.app.guiding_label.config(text=f'{percentage}%\n{self.app.last_guiding_text}')
            self.app.progress_bar.config(value=percentage)
            self.app.update_idletasks()

    def flush(self):
        pass

class ClipsMusicRemover:
    '''
    A MusicRemover class used especially to watch videos directly without music.\n
    It works with this order (see `start_processing()`):
        1. `slice_video_into_clips()`.
        2. `extract_audio()`.
        3. `separate_music()`.
        4. `merge_media()`.
        5. `save_whole_video()`.

        All 2-4 steps are done sequentially with each clip.
    '''
    def setup(self, app, progress_recorder):
        self.app = app
        self.progress_recorder = progress_recorder
    
    def start_processing(self, video_name: str, name_cases: dict[str, str], dimensions: str, duration):
        '''
        From here the game starts!
        '''
        self.video_name = video_name
        self.name_cases = name_cases
        self.clip_dimensions = dimensions 
        self.clip_duration = duration # it comes as string
        self.clip_duration = int(self.clip_duration.removesuffix('s'))

        # here the processing will start
        self.slice_video_into_clips()
        self.watch_while_process()
        
        self.watch_thread.join()
        self.save_whole_video()
        self.processing_tempdir.cleanup()

    def slice_video_into_clips(self):
        '''
        It slices the whole video into small clips with `duration` seconds (see `start_processing()`).\n
        The clips are saves in the temporary directory; So every file produced\n
        by the program will be deleted.
        '''
        self.app.gui_preparation('slice video into clips')

        # a temporary folder that holds all the files produced by the program.
        # deleted after finishing.
        self.processing_tempdir = TemporaryDirectory(
            dir=Path(__file__).parent.absolute(),
            prefix='video_remover_',
            delete=False
        )
        self.tempdir_path = Path(self.processing_tempdir.name).absolute()

        # the clips are saved at the program's temporary folder
        with self.processing_tempdir:
            slicing_process = subprocess.Popen([
                get_program_path('ffmpeg.exe'),
                '-i', self.video_name,
                '-c', 'copy',
                '-f', 'segment',
                '-segment_time', str(self.clip_duration),
                '-reset_timestamps', '1',
                self.tempdir_path / '%d.mp4',
                '-y'
            ])
            slicing_process.wait()

        self.clips = []
        self.processed_clips = queue.Queue(maxsize=1000)
        self.listed_processed_clips = []

        length = get_video_length(self.video_name)
        clips_range = list(range(0, length, self.clip_duration))

        for i in range(len(clips_range)):
            self.clips.append(f'{self.tempdir_path / str(i)}.mp4')

        # to make sure all clips in the list are existing and not 
        self.clips = get_existing_clips_only(self.clips)

    def watch_while_process(self):
        '''
        It displays the processed clips while the other clips are processed.\n
        '''
        self.process_thread = Thread(target=self.process, daemon=True)
        self.watch_thread = Thread(target=self.watch, daemon=True)
        self.process_thread.start()
        self.watch_thread.start()

    def process(self):
        '''
        It processes the clips to be purified.
        '''
        for i, clip in enumerate(self.clips):
            self.extract_audio(clip, i) 
            self.separate_music()
            self.merge_media(clip)
        
        # so the watching-thread knows that the processed clips are all watched.
        self.processed_clips.put('finished')
        
    def watch(self):
        '''
        It shows the `self.processed_clips`.
        '''
        dimension_args = get_dimension_args(self.clip_dimensions)
        while True:
            sleep(0.1) # so the loop doesn't iterate more than enough; leading the program to freez.

            clip = self.processed_clips.get()
            if clip == 'finished':
                break
            
            self.clip_playing_process = subprocess.Popen([
                get_program_path('ffplay.exe'),
                clip,
                *dimension_args,
                '-autoexit'
            ])
            self.clip_playing_process.wait()
        
    def extract_audio(self, clip: str, i: int):
        '''
        It extracts the audio from the given `clip`.
        '''
        self.app.gui_preparation('extract audio', self.clips, i)

        self.audio_name = clip.replace('.mp4', '.wav')
        # saving the extracted audio at the program's temporary folder.
        with self.processing_tempdir:
            extraction_process = subprocess.Popen([
                get_program_path('ffmpeg.exe'),
                '-i', clip,
                self.audio_name,
                '-y'
            ])
            extraction_process.wait()
    
    def separate_music(self):
        '''
        It separates the extracted audio into stems to keep on the vocals only.
        '''
        # no need for gui_preparation here
        wav, sr = load_audio(self.audio_name)

        with torch.no_grad():
            stems = apply_model(MODEL, wav.unsqueeze(0), progress=True)[0]

        save_vocals(self.tempdir_path, stems[3], sr)

    def merge_media(self, clip: str):
        '''
        It merges the vocals (no-music) audio file with the video file; Producing a clean clip.
        '''
        # no need for gui_preparation here
        output = clip.replace('.mp4', '(clean).mp4')
        # saving the pure clip at the program's temporary folder.
        with self.processing_tempdir:
            merging_process = subprocess.Popen([
                get_program_path('ffmpeg.exe'),
                '-i', clip,
                '-i', self.tempdir_path / 'vocals.wav',
                '-map', '0:v:0',
                '-map', '1:a:0',
                '-c:v', 'copy',
                '-c:a', 'aac',
                output,
                '-y'
            ])
            merging_process.wait()
            self.processed_clips.put(output) # put the new clean clip into the queue.
            self.listed_processed_clips.append(output) # append it to the list.

    def save_whole_video(self):
        '''
        It combines all the pure clips into one video.
        '''
        # write the clean-clips' paths down at the program's temporary folder on "processed clips.txt".
        with open(self.tempdir_path / 'processed clips.txt', mode='w', encoding='utf-8') as f:
            for clip in self.listed_processed_clips:
                f.write(f"file '{clip.replace('\\', '/')}'\n")

        video_saving_process = subprocess.Popen([
            get_program_path('ffmpeg.exe'),
            '-f',
            'concat',
            '-safe',
            '0', 
            '-i', self.tempdir_path / 'processed clips.txt',
            '-c', 'copy',
            self.name_cases['music removed'],
            '-y'
        ])
        video_saving_process.wait()
        print('video saved successfully')

class VideoMusicRemover:
    '''
    A MusicRemover class used especially to process a video; Producing a pure version\n
    without any music.
    '''

    def setup(self, app, progress_recorder):
        self.app = app
        self.progress_recorder = progress_recorder

    def start_processing(self, video_name, name_cases):
        '''
        From here the game starts!
        '''
        self.video_name = video_name
        self.name_cases = name_cases
        self.name_without_format = self.video_name[:self.video_name.rfind('.')] 
        self.audio_name = self.name_without_format + '.wav'
        self.audio_name = self.audio_name[self.audio_name.rfind('/')+1:]
        
        # here the processing will start
        self.extract_audio()
        self.separate_music()
        self.merge_media()
        self.processing_tempdir.cleanup()

    def extract_audio(self):
        '''
        It 
        '''
        self.app.gui_preparation('extract audio')

        self.processing_tempdir = TemporaryDirectory(
            dir=Path(__file__).parent.absolute(),
            prefix='video_remover_',
            delete=False
        )
        self.tempdir_path = Path(self.processing_tempdir.name).absolute()

        with self.processing_tempdir:
            extraction_process = subprocess.Popen([
                get_program_path('ffmpeg.exe'),
                '-i', self.video_name,
                self.tempdir_path / self.audio_name,
                '-y'
            ])
            extraction_process.wait()
    
    def separate_music(self):
        self.app.gui_preparation('separate music')

        wav, sr = load_audio(self.tempdir_path / self.audio_name)

        with redirect_stderr(self.progress_recorder):
            with torch.no_grad():
                stems = apply_model(
                    MODEL,
                    wav.unsqueeze(0),
                    split=True,         # Split into chunks
                    overlap=0.25,       # Overlap between chunks
                    progress=True
                )[0]
        
        save_vocals(self.tempdir_path, stems[3], sr)

    def merge_media(self):
        self.app.gui_preparation('merge media')

        # that prompt will replace the old polluted audio from the video with the pure one.
        merging_process = subprocess.Popen([
            get_program_path('ffmpeg.exe'),
            '-i', self.video_name,
            '-i', self.tempdir_path / 'vocals.wav',
            '-map', '0:v:0',
            '-map', '1:a:0',
            '-c:v', 'copy',
            '-c:a', 'aac',
            self.name_cases['music removed'], # the output video name
            '-y'
        ])
        merging_process.wait()

class App(Tk):
    def __init__(self):
        super().__init__()

        self.name_cases = {'music removed': '', 'underscored': '', 'original': ''}
        self.label_packing = {'fill': 'x', 'pady': (80, 40)}

        self.title('مزيل الموسيقا')
        self.config(bg=WINDOW_BG)
        self.geometry('300x320')
        self.resizable(False, False)

        self.guiding_label = Label(self, text='الرجاء تحديد المقطع لبدء المعالجة', bg=WINDOW_BG, fg=FG)
        self.guiding_label.pack(self.label_packing)

        self.select_btn = Button(self, BTN_STYLE, text='اختر المقطع', command=self.select_video)
        self.select_btn.pack(ipadx=20, ipady=5)

        self.progress_bar = Progressbar(self, length=200, orient='horizontal')
    
    def setup(self, progress_recorder: ProgressRecorder, vmr: VideoMusicRemover, cmr: ClipsMusicRemover):
        '''
        It connects every related class with the `App` class.\n
        Basically, it makes a setup.
        '''
        self.progress_recorder = progress_recorder
        self.vmr = vmr
        self.cmr = cmr

    def select_video(self):
        '''
        It manages video selection, handles the `AttributeError` if the video\n
        hasn't been selected.
        '''
        try:
            self.video_name = askopenfile(filetypes=[('مقاطع الفيديو', SUPPORTED_VIDEO_FORMATS)]).name
        except AttributeError:
            showinfo('ملاحظة', 'لم يتم اختيار المقطع')
        else:
            # underscored_name 'underscores' the file name only.
            self.underscored_name = self.video_name[:self.video_name.rfind('/')] + '_'.join(self.video_name[self.video_name.rfind('/'):].split())
            self.name_cases['original'] = self.video_name
            self.name_cases['underscored'] = self.underscored_name
            self.name_cases['music removed'] = self.video_name[:self.video_name.rfind('.')] + ' (بلا موسيقا).mp4'

            # so it doesn't show FileNotFoundError when the filename has spaces
            self.video_name = self.rename_video_to('underscored')

            self.show_processing_options()

    def show_processing_options(self):
        '''
        It gives the user options how to process the video either by saving the whole video\n
        without music, or by watching it pure through clean clips.
        '''

        self.select_btn.forget()
        self.label_packing['pady'] = (30, 30)
        # describing the two features.
        message = [
            ':هناك طريقتان للاستمتاع بالمقطع بلا موسيقا\n',
            'خيار المشاهدة الفورية: يعني مشاهدة المقطع على',
            '.شكل لقطات نقية لكي لا تنتظر فترة طويلة\n',
            'يتم حفظ المقطع خاليا من الموسيقا بعد الانتهاء',
            '.من مشاهدته كاملا\n',
            'أما خيار حفظ المقطع فهو فقط يحفظ المقطع',
            '.بعد تنقيته من الموسيقا'
        ]
        self.guiding_label.config(justify='right')
        self.update_guiding_label('\n'.join(message))

        self.direct_display_btn = Button(self, BTN_STYLE, text='مشاهدة فورية', command=lambda: self.go_to_option('show'))
        self.direct_display_btn.pack(BTN_PACKING)

        self.save_video_btn = Button(self, BTN_STYLE, text='حفظ المقطع', command=lambda: self.go_to_option('save'))
        self.save_video_btn.pack(BTN_PACKING, ipadx=24, pady=10)

    def go_to_option(self, option: str):
        '''
        It orients the program depending on the method that the user selected.
        '''
        self.guiding_label.config(justify='center')
        for btn in (self.direct_display_btn, self.save_video_btn):
            btn.forget()

        if option == 'show':
            self.show_watching_options()

        elif option == 'save':
            self.processing_thread = Thread(target=lambda: self.vmr.start_processing(self.video_name, self.name_cases), daemon=True)
            self.processing_thread.start()
            Thread(target=self.show_finishing_options, daemon=True).start()

    def show_watching_options(self):
        '''
        It shows the options of the watching method.\n
        As you selected the 'watching' method, this function will be called.\n
        '''
        self.label_packing['pady'] = (50, 30)
        self.update_guiding_label('حدد إعدادات المشاهدة')

        self.clip_dimensions = StringVar()
        self.clip_duration = StringVar()

        self.dimensions_combobox = Combobox(
            self,
            state='readonly',
            justify='center',
            textvariable=self.clip_dimensions
        )
        self.duration_combobox = Combobox(
            self,
            state='readonly',
            justify='center',
            textvariable=self.clip_duration
        )
        
        self.dimensions_combobox.config(
            values=(
                'اختر أبعاد اللقطات',
                '1280x720',
                '1920x1080',
                'وضع الشاشة الكاملة'
            )
        )
        self.duration_combobox.config(
            values=(
                'اختر مدة كل اللقطات',
                '10s',
                '60s',
            )
        )

        # set the first one as a placeholder
        self.dimensions_combobox.current(0)
        self.duration_combobox.current(0)

        self.dimensions_combobox.pack()
        self.duration_combobox.pack(pady=20)

        self.save_options_btn = Button(self, BTN_STYLE, text='بدء المشاهدة', command=self.check_selection)
        self.save_options_btn.pack(ipadx=30, ipady=3)
    
    def check_selection(self):
        '''
        It checks if all settings have been selected.\n
        If not, it will call `showerror()`.
        '''
        dimensions = self.clip_dimensions.get()
        duration = self.clip_duration.get()

        # if all settings have been set
        if dimensions != 'اختر أبعاد اللقطات' and duration != 'اختر مدة كل اللقطات':
            self.gui_preparation('select clip options')
            self.processing_thread = Thread(
                target=lambda: self.cmr.start_processing(self.video_name, self.name_cases, dimensions, duration),
                daemon=True
            )
            self.processing_thread.start()
            Thread(target=self.show_finishing_options, daemon=True).start()
        else:
            showerror('خطأ', 'يجب تحديد جميع الإعدادات')
        
    def show_finishing_options(self):
        '''
        It shows the end screen, and offers either to `open_video()`, or to `open_video_folder()`.
        '''
        self.processing_thread.join()
        
        self.progress_bar.forget()
        self.label_packing['pady'] = (50, 50)
        self.update_guiding_label('..انتهت المعالجة بنجاح')

        Button(self, BTN_STYLE, text='فتح المقطع', command=self.open_video).pack(BTN_PACKING)
        Button(self, BTN_STYLE, text='فتح موقع المقطع',
            command=self.open_video_folder).pack(BTN_PACKING, pady=10)
        
        self.video_name = self.rename_video_to('original')

    def gui_preparation(self, step_name: str = '', clips: list[str] = [], current_clip_index: int = 0):
        '''
        It prepares the GUI according to `step_name`.

        step_name should be one of the following:
            - `'select clip options'`
            - `'slice video into clips'`
            - `'extract audio'`
            - `'separate music'`
            - `'merge media'`
        '''
        appropriate_step_names = (
            'select clip options',
            'slice video into clips',
            'extract audio',
            'separate music',
            'merge media'
        )
        error_text = f'step_name is not an appropriate name.\nIt should be either:\n\t{'\nor\n\t'.join(appropriate_step_names)}'
        assert step_name in appropriate_step_names, error_text

        if step_name == 'select clip options':
            self.dimensions_combobox.forget()
            self.duration_combobox.forget()
            self.save_options_btn.forget()

        elif step_name == 'slice video into clips':
            self.label_packing['pady'] = (50, 0)
            self.update_guiding_label(f'..تقسيم المقطع')

        elif step_name == 'extract audio':
            if self.select_btn.winfo_ismapped():
                self.select_btn.forget()

            if not self.progress_bar.winfo_ismapped():
                self.progress_bar.pack(padx=20, pady=(110, 0))

            self.label_packing['pady'] = (10, 0)
            self.update_progress_bar(0)

            if clips:
                self.update_progress_bar((current_clip_index+1)/len(clips)*100)
                self.update_guiding_label(f'({current_clip_index+1}/{len(clips)}) معالجة اللقطة')
            else:
                self.update_guiding_label('..استخراج الصوت')
        
        elif step_name == 'separate music' and not clips:
            self.update_guiding_label('..فصل الموسيقا')
        
        elif step_name == 'merge media' and not clips:
            self.update_guiding_label('..دمج الصوت مع المقطع')

    def open_video(self):
        '''
        It opens the video through it's saved name in `self.name_cases['music removed']`.
        '''
        os.startfile(self.name_cases['music removed'])

    def open_video_folder(self):
        '''
        It opens the video folder through the video's saved name in `self.name_cases['original']`
        '''
        filename = self.name_cases['original']
        folder = filename[:filename.rfind('/')]
        os.startfile(folder)

    def rename_video_to(self, case: str):
        '''
        It renames video file to/from `'underscored'` and `'original'`.
        '''
        try:
            if case == 'original':
                os.rename(self.name_cases['underscored'], self.name_cases[case])
            elif case == 'underscored':
                os.rename(self.name_cases['original'], self.name_cases[case])
        except FileExistsError:
            print('\n\n\nIt seems the filename is already underscored\n\n\n')
        
        return self.name_cases[case]
        
    def update_progress_bar(self, percentage: float):
        '''
        It updates the progress bar percentage.
        '''
        self.progress_bar.config(value=percentage)
        self.update()

    def update_guiding_label(self, text: str):
        '''
        It updates the text of the guiding label.
        '''
        self.guiding_label.forget()
        self.guiding_label.config(text=text)
        self.guiding_label.pack(self.label_packing)
        self.last_guiding_text = text
        self.update()
    
def main():
    app = App()
    progress_recorder = ProgressRecorder()
    video_music_remover = VideoMusicRemover()
    clips_music_remover = ClipsMusicRemover()
    
    app.setup(
        progress_recorder=progress_recorder,
        vmr=video_music_remover,
        cmr=clips_music_remover
    )
    progress_recorder.setup(app=app)
    video_music_remover.setup(app=app, progress_recorder=progress_recorder)
    clips_music_remover.setup(app=app, progress_recorder=progress_recorder)

    app.mainloop()

if __name__ == '__main__':
    main()

