#!/bin/bash
# Video araçları (Telegram dinleyici arka planda kurar)
sudo apt-get update -qq
sudo apt-get install -y -qq ffmpeg > /dev/null
pip install -q edge-tts pillow
mkdir -p fonts
curl -fsSL -o fonts/Anton-Regular.ttf https://raw.githubusercontent.com/google/fonts/main/ofl/anton/Anton-Regular.ttf || true
curl -fsSL -o fonts/PlayfairDisplay-Italic.ttf "https://raw.githubusercontent.com/google/fonts/main/ofl/playfairdisplay/PlayfairDisplay-Italic%5Bwght%5D.ttf" || true
curl -fsSL -o fonts/Montserrat-Bold.ttf https://raw.githubusercontent.com/JulietaUla/Montserrat/master/fonts/ttf/Montserrat-Bold.ttf || true
echo TOOLS_READY > /tmp/tools_ready
