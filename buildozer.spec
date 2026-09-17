# ВАЖНО: переименуй этот файл в "buildozer.spec" (убери .txt) —
# веб-чат не умеет отдавать файлы с расширением .spec напрямую.

[app]

title = PhoneCam
package.name = phonecam
package.domain = org.example

# Все файлы проекта должны лежать в этой же папке (main.py, stream_server.py)
source.dir = .
source.include_exts = py,png,jpg,kv,atlas

version = 1.0.0

# pillow — сжатие кадра в JPEG (лёгкий рецепт, без тяжёлого opencv)
# camera4kivy + gestures4kivy — доступ к камере Android
requirements = python3,kivy==2.2.1,pillow,camera4kivy,gestures4kivy

# Подключает AndroidX/CameraX-провайдер камеры — обязательный хук для Camera4Kivy
p4a.hook = camerax_provider/gradle_options.py
android.enable_androidx = True

orientation = portrait
fullscreen = 0

android.permissions = CAMERA,INTERNET,ACCESS_NETWORK_STATE,WAKE_LOCK

# camera4kivy проверялся на API 33; если сборка ругается на API — поставь 34
android.api = 33
android.minapi = 24
android.ndk = 25b

# Только arm64: APK, собранный лишь под armeabi-v7a, падает на 64-битных телефонах
android.archs = arm64-v8a

android.accept_sdk_license = True
android.allow_backup = True

[buildozer]
log_level = 2
warn_on_root = 1
