#!/bin/bash
# Video araçları (Telegram dinleyici arka planda kurar). Hızlı olanlar önce.
pip install -q edge-tts pillow || pip install -q --break-system-packages edge-tts pillow
mkdir -p fonts
curl -fsSL -o fonts/Anton-Regular.ttf https://raw.githubusercontent.com/google/fonts/main/ofl/anton/Anton-Regular.ttf || true
curl -fsSL -o fonts/PlayfairDisplay-Italic.ttf "https://raw.githubusercontent.com/google/fonts/main/ofl/playfairdisplay/PlayfairDisplay-Italic%5Bwght%5D.ttf" || true
curl -fsSL -o fonts/Montserrat-Bold.ttf https://raw.githubusercontent.com/JulietaUla/Montserrat/master/fonts/ttf/Montserrat-Bold.ttf || true
sudo apt-get update -qq
sudo apt-get install -y -qq ffmpeg > /dev/null
python -c "import edge_tts" && command -v ffmpeg && echo TOOLS_READY > /tmp/tools_ready
