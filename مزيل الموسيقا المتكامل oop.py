from tkinter import Tk, Label, Button
from tkinter.ttk import Progressbar
from tkinter.filedialog import askopenfile
from tkinter.messagebox import showinfo
from contextlib import redirect_stderr
import os
import subprocess
import re
from queue import Queue
from time import sleep

from demucs.pretrained import get_model
from demucs.apply import apply_model
import ffmpeg
import torch
import torchaudio
import cv2
from threading import Thread

# Constants
WINDOW_BG = '#222222'
BTN_BG = '#0E6CB4'
FG = 'white'
BTN_PACKING = {'ipadx': 20, 'ipady': 5}
SUPPORTED_VIDEO_FORMATS = '*.MP4 *.MKV *.MOV *.WEBM *.AVI *.FLV'

class ProgressRecorder():
    def setup(self, app):
        self.app = app

    def write(self, text):
        match = re.search(r'(\d+)%', text)

        if match:
            percentage = int(match.group(1))
            (
            self.app.guiding_label.config(
                text=f'{percentage}%\n{self.app.last_guiding_text}'
                )
            )
            self.app.loading_bar.config(value=percentage)
            self.app.update_idletasks()

    def flush(self):
        pass

class MusicRemover:
    def __init__(self, video_name: str, is_clip: bool):
        self.video_name = video_name


class App(Tk):
    def __init__(self):
        super().__init__()

        self.rename_cases = {'music removed': '', 'underscored': '', 'original': ''}
        self.label_packing = {'fill': 'x', 'pady': (50, 50)}

        self.title('مزيل الموسيقا')
        self.config(bg=WINDOW_BG)
        self.geometry('300x300')

        self.guiding_label = Label(self, text='الرجاء تحديد المقطع لبدء المعالجة', bg=WINDOW_BG, fg=FG)
        self.guiding_label.pack(self.label_packing) 

        self.select_btn = Button(self, text='اختر المقطع', bg=BTN_BG, fg=FG, command=self.select_video)
        self.select_btn.pack(ipadx=20, ipady=5)

        self.loading_bar = Progressbar(self, length=100, orient='horizontal')

    
    def setup(self, progress_recorder):
        self.progress_recorder = progress_recorder
    
    def select_video(self):
        try:
            self.video_name = askopenfile(filetypes=[('مقاطع الفيديو', SUPPORTED_VIDEO_FORMATS)]).name
        except AttributeError:
            showinfo('ملاحظة', 'لم يتم اختيار المقطع')
        else:
            self.select_btn.forget()

            self.update_guiding_label('المشاهدة الفورية بلا موسيقا تعرض لك المقطع\n.بصورة لقطات فور الانتهاء من تنقية كل لقطة\n\n' \
                                      'أما حفظ المقطع بلا موسيقا فيكون بتنقيته\n.ثم حفظه في الجهاز بكل بساطة')        
            self.show_options()

    def show_options(self):
        self.direct_display_btn = Button(self, text='مشاهدة فورية بلا موسيقا',
                                         bg=BTN_BG, fg=FG, command=lambda: self.go_to_option('show'))
        self.direct_display_btn.pack(BTN_PACKING)

        self.process_video_btn = Button(self, text='حفظ المقطع بلا موسيقا',
                                        bg=BTN_BG, fg=FG, command=lambda: self.go_to_option('save'))
        self.process_video_btn.pack(BTN_PACKING, ipadx=24, pady=10)

    def go_to_option(self, option: str):
        for btn in (self.direct_display_btn, self.process_video_btn):
            btn.forget()

        if option == 'show':
            self.slice_video_into_clips()
        elif option == 'save':
            self.extract_audio()

    def slice_video_into_clips(self):
        self.update_guiding_label('..تقسيم المقطع')

        probe = ffmpeg.probe(self.video_name)
        length = float(probe['format']['duration'])

        self.clips = []
        self.processed_clips = Queue(maxsize=1000)

        for i, sec in enumerate(list(range(0, round(length), 10))):
            ffmpeg.input(self.video_name, ss=sec, t=10).output(f'{i}.mp4').run(overwrite_output=True)
            self.clips.append(f'{i}.mp4')

        self.threaded_watchWhileProcess()

    def threaded_watchWhileProcess(self):
        Thread(target=self.watch_while_process, daemon=True).start()

    def watch_while_process(self):
        Thread(target=self.process, daemon=True).start()
        Thread(target=self.watch, daemon=True).start()

    def process(self):
        for i, clip in enumerate(self.clips):
            self.extract_audio(i, clip) # it continues all the process sequentially
        
        self.processed_clips.put('finished')

    def watch(self):
        while True:
            sleep(0.1)

            clip = self.processed_clips.get(timeout=12)
            if clip == 'finished':
                break

            self.play_clip_process = subprocess.Popen([
                'ffplay',
                '-x', '1280',
                '-y', '720',
                '-autoexit',
                clip
            ])
            self.play_clip_process.wait()

    def extract_audio(self, i = 0, clip = None):
        self.label_packing['pady'] = (10, 0)

        if not self.loading_bar.winfo_ismapped():
            self.loading_bar.pack(ipadx=40, padx=20, pady=(100, 0))

        if clip:
            self.video_name = clip
            self.update_guiding_label(f'{i+1}/{len(self.clips)}')
            self.update_progress_bar(i/len(self.clips)*100)
            self.update_idletasks()
        else:
            underscored_name = '_'.join(self.video_name.split())
            self.rename_cases['original'] = self.video_name
            self.rename_cases['underscored'] = underscored_name
            self.rename_cases['music removed'] = self.video_name[:self.video_name.find('.')] + '(بلا موسيقا).mp4'
            self.video_name = self.rename_video_to('underscored')

            self.update_guiding_label('..استخراج الصوت')

        if self.select_btn.winfo_ismapped():
            self.select_btn.forget()

        self.name_without_format, self.video_format = self.video_name[:self.video_name.find('.')], self.video_name[self.video_name.rfind('.'):]
        self.audio_name = self.name_without_format + '.wav'
        (
            ffmpeg.input(self.video_name)
            .output(self.audio_name)
            .run(overwrite_output=True)
        )
        self.separate_music(clip)
    
    def separate_music(self, clip = None):
        model = get_model('htdemucs')
        model.eval()
        wav, sr = torchaudio.load(self.audio_name)

        if clip:
            with torch.no_grad():
                stems = apply_model(model, wav.unsqueeze(0), progress=True)[0]
        else:            
            self.update_guiding_label('..فصل الموسيقا')

            with redirect_stderr(self.progress_recorder):
                with torch.no_grad():
                    if float(ffmpeg.probe(self.audio_name)['format']['duration'])//60 <= 1:
                        stems = apply_model(model, wav.unsqueeze(0), split=True, progress=True)[0]
                    else:
                        stems = apply_model(
                            model,
                            wav.unsqueeze(0),
                            split=True,         # Split into chunks
                            overlap=0.25,       # Overlap between chunks
                            progress=True
                        )[0]
        
        torchaudio.save('vocals.wav', stems[3], sr)
        self.merge_media(clip)

    def merge_media(self, clip = None):
        video_input = ffmpeg.input(self.name_without_format + self.video_format)
        audio_input = ffmpeg.input('vocals.wav')

        if clip:
            (
                ffmpeg.concat(video_input, audio_input, v=1, a=1)
                .output(self.name_without_format+'(بلا موسيقا).mp4')
                .run(overwrite_output=True)
            )
            self.processed_clips.put(self.name_without_format+'(بلا موسيقا).mp4')
        else:
            self.update_guiding_label('..دمج الصوت مع المقطع')
            (
                ffmpeg.concat(video_input, audio_input, v=1, a=1)
                .output(self.rename_cases["music removed"])
                .run(overwrite_output=True)
            )
            self.loading_bar.forget()
            self.label_packing['pady'] = (50, 50)
            self.update_guiding_label('..انتهت المعالجة بنجاح')

            Button(self, text='فتح المقطع', bg=BTN_BG, fg=FG, command=self.open_video).pack(BTN_PACKING)
            Button(self, text='فتح موقع المقطع', bg=BTN_BG, fg=FG,
                command=self.open_video_folder).pack(BTN_PACKING, pady=10)
            
        self.delete_temporary_files()

    def open_video(self):
        os.startfile(self.rename_cases['music removed'])

    def open_video_folder(self):
        folder = self.video_name[:self.video_name.rfind('/')]
        os.startfile(folder)

    def rename_video_to(self, case: str):
        os.rename(self.video_name, self.rename_cases[case])
        return self.rename_cases[case]

    def delete_temporary_files(self):
        os.remove('vocals.wav')

        
    def update_progress_bar(self, percentage: float):
        self.loading_bar.config(value=percentage)

    def update_guiding_label(self, text: str):
        self.guiding_label.forget()
        self.guiding_label.config(text=text)
        self.guiding_label.pack(self.label_packing)
        self.last_guiding_text = text
        self.update()

def main():
    app = App()
    progress_recorder = ProgressRecorder()
    
    app.setup(progress_recorder=progress_recorder)
    progress_recorder.setup(app=app)

    app.mainloop()

if __name__ == '__main__':
    main()

