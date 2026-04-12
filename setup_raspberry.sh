#!/bin/bash

echo "================================="
echo "  J-KAI — Setup Raspberry Pi 5"
echo "================================="

echo ""
echo ">>> Mise à jour du système..."
sudo apt update && sudo apt upgrade -y

echo ""
echo ">>> Installation de Python et pip..."
sudo apt install -y python3 python3-pip python3-venv

echo ""
echo ">>> Installation de Git..."
sudo apt install -y git

echo ""
echo ">>> Installation de ffmpeg (pour la voix)..."
sudo apt install -y ffmpeg

echo ""
echo ">>> Installation de PortAudio (pour le micro)..."
sudo apt install -y portaudio19-dev python3-pyaudio

echo ""
echo ">>> Clonage du projet JKAI..."
git clone https://github.com/Sethu928/JKAI.git
cd JKAI

echo ""
echo ">>> Installation des dépendances Python..."
pip3 install openai flask flask-cors python-dotenv openai-whisper pyttsx3 pyaudio sounddevice soundfile

echo ""
echo ">>> Création du fichier .env..."
echo "Entre ta clé OpenAI :"
read -p "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=" API_KEY
echo "OPENAI_API_KEY=$API_KEY" > .env

echo ""
echo ">>> Configuration du démarrage automatique..."
SERVICE="[Unit]
Description=J-KAI Nexus
After=network.target

[Service]
ExecStart=/usr/bin/python3 /home/pi/JKAI/server.py
WorkingDirectory=/home/pi/JKAI
StandardOutput=inherit
StandardError=inherit
Restart=always
User=pi

[Install]
WantedBy=multi-user.target"

echo "$SERVICE" | sudo tee /etc/systemd/system/jkai.service
sudo systemctl daemon-reload
sudo systemctl enable jkai
sudo systemctl start jkai

echo ""
echo "================================="
echo "  J-KAI est en ligne sur le Pi !"
echo "  Accède via : http://[IP_DU_PI]:5000"
echo "================================="