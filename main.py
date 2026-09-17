"""
PhoneCam — телефон как IP-камера (приложение для Android на Kivy).

Что делает:
  * открывает камеру телефона и показывает живое превью на экране;
  * каждый кадр сжимает в JPEG и отдаёт в MJPEGServer;
  * сервер раздаёт поток по HTTP -> смотреть можно в браузере и в OpenCV-клиенте.

Адреса, пока приложение запущено:
    http://<IP-телефона>:8080/             — страница с потоком
    http://<IP-телефона>:8080/stream.mjpg  — MJPEG-поток (читает camera_viewer.py)
    http://<IP-телефона>:8080/snapshot.jpg — одиночный кадр

Сборка в APK: см. README.md (buildozer -v android debug).
"""

import io
import time

from kivy.app import App
from kivy.clock import Clock
from kivy.lang import Builder
from kivy.properties import StringProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.utils import platform
from PIL import Image

from camera4kivy import Preview
from stream_server import MJPEGServer, local_ip

# ------------------------------ настройки ------------------------------------
PORT = 8080           # порт HTTP-сервера
PASSWORD = None       # например 'qwerty123' — включит Basic Auth на поток
JPEG_QUALITY = 70     # 40..85: ниже — быстрее и меньше лагов
STREAM_FPS = 15       # сколько кадров в секунду раздаём клиентам
ROTATE = 0            # 0 / 90 / 180 / 270 — если картинка лежит на боку
ANALYZE_RES = 720     # длинная сторона кадра анализа: 480 / 720 / 1080
CAMERA_ID = 'back'    # 'back' или 'front'
# -----------------------------------------------------------------------------

SERVER = None  # создаётся в PhoneCamApp.build() ДО создания виджетов


def keep_screen_on():
    """Чтобы экран не гас, пока идёт раздача."""
    if platform != 'android':
        return
    try:
        from android import mActivity
        from jnius import autoclass
        flags = autoclass('android.view.WindowManager$LayoutParams')
        mActivity.getWindow().addFlags(flags.FLAG_KEEP_SCREEN_ON)
    except Exception as exc:  # не критично
        print('keep_screen_on:', exc)


class CamStream(Preview):
    """Preview, который дополнительно отдаёт кадры в MJPEG-сервер.

    analyze_pixels_callback вызывается Camera4Kivy НЕ в UI-потоке,
    поэтому сжатие в JPEG можно делать прямо здесь, без лагов интерфейса.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.server = SERVER
        self.fps = 0.0
        self._frames = 0
        self._t0 = time.time()

    def analyze_pixels_callback(self, pixels, image_size, image_pos, scale, mirror):
        # pixels     : bytes, RGBA подряд (не зеркалится)
        # image_size : (width, height) кадра анализа
        w, h = image_size
        need = w * h * 4
        if len(pixels) < need:      # подстраховка от нестандартного stride
            return

        img = Image.frombytes('RGBA', (w, h), pixels[:need])
        if ROTATE:
            img = img.rotate(-ROTATE, expand=True)   # минус = по часовой стрелке

        buf = io.BytesIO()
        img.convert('RGB').save(buf, format='JPEG', quality=JPEG_QUALITY)
        if self.server:
            self.server.set_frame(buf.getvalue())

        self._frames += 1
        now = time.time()
        if now - self._t0 >= 1.0:
            self.fps = self._frames / (now - self._t0)
            self._frames = 0
            self._t0 = now


Builder.load_string('''
<Root>:
    orientation: 'vertical'
    CamStream:
        id: cam
        aspect_ratio: '16:9'
    BoxLayout:
        orientation: 'vertical'
        size_hint_y: None
        height: dp(170)
        padding: dp(10)
        spacing: dp(6)
        Label:
            text: root.info
            font_size: '15sp'
            halign: 'center'
            valign: 'middle'
            text_size: self.size
        Label:
            text: root.status
            font_size: '14sp'
            color: 0.72, 0.76, 0.84, 1
        BoxLayout:
            spacing: dp(8)
            size_hint_y: None
            height: dp(48)
            Button:
                text: 'Сменить камеру'
                on_release: root.switch_camera()
            Button:
                text: 'Пауза / пуск'
                on_release: root.toggle_run()
''')


class Root(BoxLayout):
    info = StringProperty('Запускаю...')
    status = StringProperty('')

    def connect_camera(self):
        self.ids.cam.connect_camera(camera_id=CAMERA_ID,
                                    analyze_pixels_resolution=ANALYZE_RES,
                                    enable_analyze_pixels=True,
                                    enable_video=False)

    def switch_camera(self):
        self.ids.cam.select_camera('toggle')

    def toggle_run(self):
        cam = self.ids.cam
        if cam.camera_connected:
            cam.disconnect_camera()
        else:
            self.connect_camera()


class PhoneCamApp(App):
    def build(self):
        global SERVER
        SERVER = MJPEGServer(port=PORT, fps=STREAM_FPS, password=PASSWORD)
        self.title = 'Phone IP Camera'
        self.ui = Root()
        return self.ui

    def on_start(self):
        keep_screen_on()
        SERVER.start()
        ip = local_ip()
        self.ui.info = ('Поток: http://%s:%d/\nВ клиенте: %s:%d'
                        % (ip, PORT, ip, PORT))
        self._ask_permissions()
        Clock.schedule_interval(self._refresh, 1.0)

    def _ask_permissions(self):
        if platform == 'android':
            from android.permissions import Permission, request_permissions

            def granted(*_args):
                Clock.schedule_once(self._connect, 0.7)

            request_permissions([Permission.CAMERA,
                                 Permission.INTERNET,
                                 Permission.ACCESS_NETWORK_STATE], granted)
        else:
            Clock.schedule_once(self._connect, 0.7)

    def _connect(self, _dt):
        try:
            self.ui.connect_camera()
        except Exception as exc:
            self.ui.status = 'Ошибка камеры: %s' % exc

    def _refresh(self, _dt):
        if self.ui.ids.cam.camera_connected:
            self.ui.status = ('камера вкл · %.1f кадр/с · клиентов: %d'
                              % (self.ui.ids.cam.fps, SERVER.clients))
        else:
            self.ui.status = 'камера выкл'

    def on_stop(self):
        try:
            self.ui.ids.cam.disconnect_camera()
        except Exception:
            pass
        SERVER.stop()


if __name__ in ('__main__', '__android__'):
    PhoneCamApp().run()
