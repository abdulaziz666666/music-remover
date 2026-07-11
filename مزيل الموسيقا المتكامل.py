
# This is an old version of the program. It is procedural oriented but it doesn't have the same features the OOP version has.
# Also, it may show some errors as it is not maintained.

from tkinter import Tk, Label, Button, IntVar
from tkinter.ttk import Progressbar
from tkinter.filedialog import askopenfile
from tkinter.messagebox import showerror
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

rename_cases = {'music removed': '', 'underscored': '', 'original': ''}
label_packing = {'fill': 'x', 'pady': (50, 50)}


class ProgressRecorder():
    def write(self, text):
        match = re.search(r'(\d+)%', text)

        if match:
            percentage = int(match.group(1))
            print(f'{percentage}%')
            loading_bar.config(value=percentage)
            window.update_idletasks()

    def flush(self):
        pass

def select_video():
    global rename_cases, video_name

    try:
        video_name = askopenfile(filetypes=[('مقاطع الفيديو', SUPPORTED_VIDEO_FORMATS)]).name
    except AttributeError:
        print('The user did not select the video.')
    else:

        underscored_name = '_'.join(video_name.split())
        
        rename_cases['original'] = video_name
        rename_cases['underscored'] = underscored_name
        rename_cases['music removed'] = video_name[:video_name.find('.')] + '(بلا موسيقا).mp4'

        video_name = rename_video_to('underscored')
        extract_audio(video_name)


def extract_audio(video_name: str):
    global name_without_format, video_format, label_packing

    label_packing['pady'] = (10, 0)
    select_btn.forget()
    update_guiding_label('..استخراج الصوت')

    name_without_format, video_format = video_name[:video_name.find('.')], video_name[video_name.rfind('.'):]
    audio_name = name_without_format + '.wav'
    (
        ffmpeg.input(video_name)
        .output(audio_name)
        .run(overwrite_output=True)
    )
    separate_music(audio_name)

def separate_music(audio_name):
    loading_bar.pack(ipadx=40, padx=20, pady=(100, 0))
    
    update_guiding_label('..فصل الموسيقا')
    
    model = get_model('htdemucs')
    model.eval()

    wav, sr = torchaudio.load(audio_name)
    with redirect_stderr(ProgressRecorder()):
        
        with torch.no_grad():
            if float(ffmpeg.probe(audio_name)['format']['duration'])//60 <= 1:
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
    merge_media()

def merge_media():
    global name_without_format, video_format, label_packing

    update_guiding_label('..دمج الصوت مع المقطع')

    video_input = ffmpeg.input(name_without_format + video_format)
    audio_input = ffmpeg.input(name_without_format + '.wav')
    (
        ffmpeg.concat(video_input, audio_input, v=1, a=1)
        .output(rename_cases["music removed"])
        .run(overwrite_output=True)
    )
    
    rename_video_to('original')

    delete_temporary_files()

    loading_bar.forget()
    label_packing['pady'] = (50, 20)
    update_guiding_label('..انتهت المعالجة بنجاح')
    Button(window, text='فتح المقطع', bg=BTN_BG, fg=FG, command=open_video).pack(BTN_PACKING)
    Button(window, text='فتح موقع المقطع', bg=BTN_BG, fg=FG,
           command=open_video_folder).pack(BTN_PACKING, pady=10)

def open_video():
    global video_name
    os.system(video_name)

def open_video_folder():
    global video_name
    folder = video_name[:video_name.rfind('/')]
    os.system(f'explorer "{folder}"')

def rename_video_to(case: str):
    global video_name
    os.rename(video_name, rename_cases[case])
    print(f'new name:\n{video_name}')
    return rename_cases[case]

def delete_temporary_files():
    os.remove('vocals.wav')
    
def update_progress_bar(percentage: int):
    loading_bar['value'] = percentage

def update_guiding_label(text: str):
    guiding_label.forget()
    guiding_label.pack(label_packing)
    guiding_label.config(text=text)
    window.update()


window = Tk()
window.title('مزيل الموسيقا')
window.config(bg=WINDOW_BG)
window.geometry('300x300')

guiding_label = Label(window, text='الرجاء تحديد المقطع لبدء المعالجة', bg=WINDOW_BG, fg=FG)
guiding_label.pack(label_packing) 

select_btn = Button(window, text='اختر المقطع', bg=BTN_BG, fg=FG, command=select_video)
select_btn.pack(ipadx=20, ipady=5)

loading_bar = Progressbar(window, length=100, orient='horizontal')

window.mainloop()


