from flask import Flask, request, jsonify
import requests
import traceback
import logging
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from io import BytesIO

app = Flask(__name__)

SERVICE_ACCOUNT_FILE = 'runpod-431523-a1c91cacc1a7.json'
SCOPES = ['https://www.googleapis.com/auth/drive.file']


def get_drive_service():
    credentials = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    service = build('drive', 'v3', credentials=credentials)
    return service


load_dotenv()


def get_db_connection():
    connection_string = os.getenv("DATABASE_URL")
    conn = psycopg2.connect(connection_string)
    return conn


def upload_audio_to_drive(file):
    file_stream = BytesIO(file.read())
    
    service = get_drive_service()
    file_metadata = {'name': file.filename}
    media = MediaIoBaseUpload(file_stream, mimetype='audio/mp3')
    drive_file = service.files().create(body=file_metadata, media_body=media, fields='id').execute()
    
    file_id = drive_file.get('id')
    
    
    permission = {
        'type': 'anyone',
        'role': 'reader',
    }
    service.permissions().create(
        fileId=file_id,
        body=permission
    ).execute()
    
    # Generate a shareable link
    file_url = f"https://drive.google.com/uc?id={file_id}"
    
    return file_id, file_url


def transcribe_audio(file_url):
    runpod_api_url = os.getenv("RUNPOD_API_URL")
    runpod_api_token = os.getenv("RUNPOD_API_TOKEN")
    
    headers = {
        'Authorization': f'Bearer {runpod_api_token}',
    }
    payload = {
        "input": {
            "audio": file_url
        }
    }
    response = requests.post(runpod_api_url, headers=headers, json=payload)
    response.raise_for_status()  
    
    return response.json()

@app.route('/upload', methods=['POST'])
def upload():
    audio = request.files.get('audio')
    format_style = request.form.get('format_style')
    language = request.form.get('language')
    
    if not audio or not format_style or not language:
        return jsonify({"error": "Missing required fields"}), 400

    try:
        # Upload audio and transcribe
        file_id, file_url = upload_audio_to_drive(audio)
        transcription_response = transcribe_audio(file_url)
        
        if 'output' in transcription_response:
            transcription = transcription_response['output']['transcription']
        else:
            return jsonify({"error": "Transcription failed or response is malformed"}), 500
        
        # Save to database
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO audio_files (file_id, file_url, format_style, language) VALUES (%s, %s, %s, %s) RETURNING id",
            (file_id, file_url, format_style, language)
        )
        db_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()

        return jsonify({
            "file_url": file_url,
            "transcribed_text": transcription,
            "format_style": format_style,
            "language": language
        })
    
    except Exception as e:
        error_message = str(e)
        detailed_error = traceback.format_exc()
        app.logger.error(f"Error: {error_message}")
        app.logger.error(f"Traceback: {detailed_error}")
        return jsonify({"error": error_message, "details": detailed_error}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))  # Use the PORT env var or default to 5000
    app.run(host='0.0.0.0', port=port, debug=True)
