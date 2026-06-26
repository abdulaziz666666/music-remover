from tkinter import Tk, Label, Button
from tkinter.ttk import Progressbar
from tkinter.filedialog import askopenfile
from tkinter.messagebox import showinfo
from contextlib import redirect_stderr
import os
import re

from demucs.pretrained import get_model
from demucs.apply import apply_model
import ffmpeg
import torch
import torchaudio

# Constants
WINDOW_BG = '#222222'
BTN_BG = '#0E6CB4'
FG = 'white'
BTN_PACKING = {'ipadx': 20, 'ipady': 5}
SUPPORTED_VIDEO_FORMATS = '*.MP4 *.MKV *.MOV *.WEBM *.AVI *.FLV'

class ProgressRecorder():
    def setup(self, music_remover):
        self.music_remover = music_remover

    def write(self, text):
        match = re.search(r'(\d+)%', text)

        if match:
            percentage = int(match.group(1))
            (
            self.music_remover.guiding_label.config(
                text=f'{percentage}%\n{self.music_remover.last_guiding_text}'
                )
            )
            self.music_remover.loading_bar.config(value=percentage)
            self.music_remover.update_idletasks()

    def flush(self):
        pass

class MusicRemover(Tk):
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
            underscored_name = '_'.join(self.video_name.split())
            
            self.rename_cases['original'] = self.video_name
            self.rename_cases['underscored'] = underscored_name
            self.rename_cases['music removed'] = self.video_name[:self.video_name.find('.')] + '(بلا موسيقا).mp4'

            self.video_name = self.rename_video_to('underscored')
            self.extract_audio()

    def show_options(self):
        self.direct_display_btn = Button(self, text='مشاهدة فورية بلا موسيقا', bg=BTN_BG, fg=FG)
        self.direct_display_btn.pack(BTN_PACKING)

        self.process_video_btn = Button(self, text='حفظ المقطع بلا موسيقا',
                                        bg=BTN_BG, fg=FG, command=self.extract_audio)
        self.process_video_btn.pack(BTN_PACKING)


    def slice_video_into_clips(self):
        probe = ffmpeg.probe(self.video_name)
        length = float(probe['format']['duration'])

        clips = []
        last_sec = 0
        last_index = 0

        for i, sec in enumerate(list(range(0, round(length), 10))):
            ffmpeg.input(self.video_name, ss=sec, t=10).output(f'{i}.mp4').run()
            last_sec = sec
            last_index = i
            clips.append(f'{i}.mp4') 

        ffmpeg.input(self.video_name, ss=last_sec, t=length-last_sec).output(f'{last_index+1}.mp4').run()
        clips.append(f'{last_index+1}.mp4')
    
    def watch_while_procces(self):
        

    def extract_audio(self):
        self.label_packing['pady'] = (10, 0)
        self.select_btn.forget()
        self.update_guiding_label('..استخراج الصوت')

        self.name_without_format, self.video_format = self.video_name[:self.video_name.find('.')], self.video_name[self.video_name.rfind('.'):]
        self.audio_name = self.name_without_format + '.wav'
        (
            ffmpeg.input(self.video_name)
            .output(self.audio_name)
            .run(overwrite_output=True)
        )
        self.separate_music()
    
    def separate_music(self):
        self.loading_bar.pack(ipadx=40, padx=20, pady=(100, 0))
        
        self.update_guiding_label('..فصل الموسيقا')
        
        model = get_model('htdemucs')
        model.eval()

        wav, sr = torchaudio.load(self.audio_name)
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
        
        torchaudio.save("vocals.wav", stems[3], sr)
        self.merge_media()

    def merge_media(self):
        self.update_guiding_label('..دمج الصوت مع المقطع')

        video_input = ffmpeg.input(self.name_without_format + self.video_format)
        audio_input = ffmpeg.input(self.name_without_format + '.wav')
        (
            ffmpeg.concat(video_input, audio_input, v=1, a=1)
            .output(self.rename_cases["music removed"])
            .run(overwrite_output=True)
        )
        
        self.rename_video_to('original')

        self.delete_temporary_files()

        self.loading_bar.forget()
        self.label_packing['pady'] = (50, 50)
        self.update_guiding_label('..انتهت المعالجة بنجاح')

        Button(self, text='فتح المقطع', bg=BTN_BG, fg=FG, command=self.open_video).pack(BTN_PACKING)
        Button(self, text='فتح موقع المقطع', bg=BTN_BG, fg=FG,
            command=self.open_video_folder).pack(BTN_PACKING, pady=10)

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
        
    def update_progress_bar(self, percentage: int):
        self.loading_bar['value'] = percentage

    def update_guiding_label(self, text: str):
        self.guiding_label.forget()
        self.guiding_label.config(text=text)
        self.guiding_label.pack(self.label_packing)
        self.last_guiding_text = text
        self.update()

def main():
    music_remover = MusicRemover()
    progress_recorder = ProgressRecorder()
    
    music_remover.setup(progress_recorder=progress_recorder)
    progress_recorder.setup(music_remover=music_remover)

    music_remover.mainloop()

if __name__ == '__main__':
    main()

