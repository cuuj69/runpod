from flask import Flask, request, jsonify
import requests
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

# Create a Drive service instance
def get_drive_service():
    credentials = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    service = build('drive', 'v3', credentials=credentials)
    return service

# Load environment variables from .env file
load_dotenv()

# Database connection
def get_db_connection():
    connection_string = os.getenv("DATABASE_URL")
    conn = psycopg2.connect(connection_string)
    return conn

# Function to upload audio file to Google Drive
def upload_audio_to_drive(file):
    file_stream = BytesIO(file.read())
    
    service = get_drive_service()
    file_metadata = {'name': file.filename}
    media = MediaIoBaseUpload(file_stream, mimetype='audio/mp3')
    drive_file = service.files().create(body=file_metadata, media_body=media, fields='id').execute()
    
    file_id = drive_file.get('id')
    
    # Make the file public
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

# Function to transcribe audio using RunPod API
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
    response.raise_for_status()  # Raise an error for bad HTTP responses
    
    return response.json()

@app.route('/upload', methods=['POST'])
def upload():
    audio = request.files.get('audio')
    format_style = request.form.get('format_style')
    language = request.form.get('language')
    
    if not audio or not format_style or not language:
        return jsonify({"error": "Missing required fields"}), 400

    try:
        # Upload file to Google Drive and get the ID and URL
        file_id, file_url = upload_audio_to_drive(audio)
        
        # Save the file URL and metadata to the database
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

        # Return the file ID and URL to the user
        return jsonify({"file_id": db_id, "file_url": file_url})
    
    except Exception as e:
        app.logger.error("An error occurred in upload route. Exception: %s", str(e))
        return jsonify({"error": "Internal server error occurred. Please try again later."}), 500

@app.route('/transcribe/<int:file_id>', methods=['GET'])
def transcribe(file_id):
    try:
        # Fetch the file URL from the database
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT file_url FROM audio_files WHERE id = %s", (file_id,))
        file_url_row = cur.fetchone()
        
        if not file_url_row:
            return jsonify({"error": "File not found"}), 404
        
        file_url = file_url_row[0]
        cur.close()
        conn.close()
        
        # Transcribe audio using RunPod API
        runpod_response = transcribe_audio(file_url)
        
        if 'output' in runpod_response:
            transcription = runpod_response['output']['transcription']
        else:
            return jsonify({"error": "Transcription failed or response is malformed"}), 500
        
        # Store transcription in the database
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO transcriptions (file_id, transcription) VALUES (%s, %s)",
            (file_id, transcription)
        )
        conn.commit()
        cur.close()
        conn.close()
        
        # Return the transcription
        return jsonify({"transcribed_text": transcription})
    
    except Exception as e:
        app.logger.error("An error occurred in transcribe route. Exception: %s", str(e))
        return jsonify({"error": "Internal server error occurred. Please try again later."}), 500

if __name__ == '__main__':
    app.run(debug=True)
