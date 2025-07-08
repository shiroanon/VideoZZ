# app.py
from flask import Flask, request, jsonify, render_template, send_from_directory
import os
import uuid
import threading
import shutil
import subprocess
import logging
from werkzeug.utils import secure_filename
# Import your montage creation script
import montage_maker

# Configuration
UPLOAD_FOLDER = 'uploads'
OUTPUT_FOLDER = 'outputs'
SERVER_VIDEOS_FOLDER = 'server_videos' # Folder for persistent videos
YT_DOWNLOADS_FOLDER = os.path.join(UPLOAD_FOLDER, 'yt_downloads')

ALLOWED_EXTENSIONS_VIDEO = {'mp4', 'mov', 'avi', 'mkv', 'webm'}
ALLOWED_EXTENSIONS_AUDIO = {'mp3', 'wav', 'aac', 'ogg', 'flac'}
ALLOWED_EXTENSIONS_LABELS = {'txt'}
YT_DLP_PATH = None

app = Flask(__name__)
app.config.from_mapping({
    'UPLOAD_FOLDER': UPLOAD_FOLDER,
    'OUTPUT_FOLDER': OUTPUT_FOLDER,
    'SERVER_VIDEOS_FOLDER': SERVER_VIDEOS_FOLDER,
    'YT_DOWNLOADS_FOLDER': YT_DOWNLOADS_FOLDER,
    'MAX_CONTENT_LENGTH': 5 * 1024 * 1024 * 1024 # 5 GB limit
})

# Ensure directories exist
for folder in [UPLOAD_FOLDER, OUTPUT_FOLDER, YT_DOWNLOADS_FOLDER, SERVER_VIDEOS_FOLDER]:
    os.makedirs(folder, exist_ok=True)

# Setup basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
app.logger.handlers.extend(logging.getLogger().handlers)

tasks = {} # In-memory store for task statuses

def allowed_file(filename, allowed_extensions):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed_extensions

def update_task_progress(task_id, progress_data):
    if task_id in tasks:
        tasks[task_id].update(progress_data)
        app.logger.info(f"Task {task_id} Progress: {progress_data}")

def run_yt_dlp_download(video_url, download_path, task_id):
    yt_dlp_executable = YT_DLP_PATH or "yt-dlp"
    cmd = [
        yt_dlp_executable, '-f', 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best',
        '--merge-output-format', 'mp4', '-o', os.path.join(download_path, '%(title)s.%(ext)s'), video_url
    ]
    app.logger.info(f"Executing yt-dlp: {' '.join(cmd)}")
    update_task_progress(task_id, {"status": "processing", "message": "Downloading video from URL..."})
    
    process = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if process.returncode != 0:
        error_message = f"yt-dlp error: {process.stderr[:500]}"
        update_task_progress(task_id, {"status": "error", "message": error_message})
        raise montage_maker.MontageError(error_message)
        
    video_files = [f for f in os.listdir(download_path) if allowed_file(f, ALLOWED_EXTENSIONS_VIDEO)]
    if not video_files:
        raise montage_maker.MontageError("yt-dlp ran, but no video file was found.")
        
    full_video_path = os.path.join(download_path, video_files[0])
    update_task_progress(task_id, {"status": "processing", "message": f"Video '{video_files[0]}' downloaded."})
    return full_video_path

def process_montage_task(task_id, input_video_path, label_file_path, output_video_name, options):
    try:
        update_task_progress(task_id, {"status": "processing", "message": "Preparing for montage generation..."})
        output_dir = os.path.join(app.config['OUTPUT_FOLDER'], task_id)
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, output_video_name)

        montage_maker.create_rhythmic_montage_ffmpeg(
            input_video_path=input_video_path,
            output_video_path=output_path,
            label_file_path=label_file_path,
            output_resolution_str=options['resolution'],
            target_total_output_duration_sec=options['total_duration'],
            target_total_num_scenes=options['total_scenes'],
            min_beat_grouped_scene_duration=options['min_scene_duration'],
            audio_file_path=options.get('audio_file_path'),
            audio_mix_behavior=options['audio_mode'],
            progress_callback=update_task_progress,
            task_id=task_id
        )
    except Exception as e:
        app.logger.error(f"Error in task {task_id}: {e}", exc_info=True)
        update_task_progress(task_id, {"status": "error", "message": str(e)})
    finally:
        # --- SAFE CLEANUP ---
        # This only cleans up temporary folders inside 'uploads/'
        # It will NOT touch the 'server_videos' folder.
        dirs_to_cleanup = set()
        
        # The task-specific directory holds transient files (labels, audio, uploaded videos)
        task_upload_dir = os.path.dirname(label_file_path)
        dirs_to_cleanup.add(task_upload_dir)

        # The yt-dlp download directory is also temporary
        if 'yt_downloads' in input_video_path:
             dirs_to_cleanup.add(os.path.dirname(input_video_path))
        
        for dir_path in dirs_to_cleanup:
            if os.path.exists(dir_path):
                shutil.rmtree(dir_path, ignore_errors=True)
                app.logger.info(f"Cleaned up temporary directory: {dir_path}")

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/list_server_videos')
def list_server_videos():
    try:
        video_files = [
            f for f in os.listdir(app.config['SERVER_VIDEOS_FOLDER'])
            if os.path.isfile(os.path.join(app.config['SERVER_VIDEOS_FOLDER'], f))
            and allowed_file(f, ALLOWED_EXTENSIONS_VIDEO)
        ]
        return jsonify(sorted(video_files))
    except Exception as e:
        app.logger.error(f"Error listing server videos: {e}")
        return jsonify({"error": "Could not list server videos."}), 500

@app.route('/create_montage', methods=['POST'])
def create_montage_route():
    task_id = str(uuid.uuid4())
    try:
        # --- ROBUST INPUT VALIDATION ---
        video_url = request.form.get('input_video_url')
        video_file = request.files.get('input_video_file')
        server_file = request.form.get('server_video_filename')

        source_count = sum([
            1 if video_url else 0,
            1 if video_file and video_file.filename else 0,
            1 if server_file else 0
        ])

        if source_count != 1:
            return jsonify({"error": "Please provide exactly ONE video source (URL, Upload, or Server File)."}), 400

        # --- Handle required label file ---
        label_file = request.files.get('label_file')
        if not label_file or not label_file.filename:
            return jsonify({"error": "A label file is required."}), 400

        # --- Save transient files to a unique temp folder ---
        task_temp_dir = os.path.join(app.config['UPLOAD_FOLDER'], task_id)
        os.makedirs(task_temp_dir)
        
        label_path = os.path.join(task_temp_dir, secure_filename(label_file.filename))
        label_file.save(label_path)

        # --- Determine video path ---
        video_path = None
        if server_file:
            video_path = os.path.join(app.config['SERVER_VIDEOS_FOLDER'], secure_filename(server_file))
            if not os.path.exists(video_path):
                 return jsonify({"error": f"Server file '{server_file}' not found."}), 404
        elif video_url:
            yt_dlp_dir = os.path.join(app.config['YT_DOWNLOADS_FOLDER'], task_id)
            os.makedirs(yt_dlp_dir)
            video_path = run_yt_dlp_download(video_url, yt_dlp_dir, task_id)
        elif video_file:
            video_path = os.path.join(task_temp_dir, secure_filename(video_file.filename))
            video_file.save(video_path)

        # --- Handle optional audio file ---
        audio_path = None
        audio_file = request.files.get('audio_file')
        if audio_file and audio_file.filename:
            audio_path = os.path.join(task_temp_dir, secure_filename(audio_file.filename))
            audio_file.save(audio_path)
        
        # --- Gather options and start task ---
        options = {
            "resolution": request.form.get('resolution', '1280x720'),
            "total_duration": float(request.form.get('total_duration', 0)),
            "total_scenes": int(request.form.get('total_scenes', 0)),
            "min_scene_duration": float(request.form.get('min_scene_duration', 0.6)),
            "audio_mode": request.form.get('audio_mode', 'replace'),
            "audio_file_path": audio_path
        }
        
        output_name = f"montage_output_{task_id}.mp4"
        tasks[task_id] = {"status": "queued", "message": "Task queued.", "task_id": task_id}
        
        thread = threading.Thread(target=process_montage_task, args=(
            task_id, video_path, label_path, output_name, options
        ))
        thread.start()

        return jsonify({"task_id": task_id, "message": "Processing initiated..."}), 202

    except Exception as e:
        app.logger.error(f"Error in /create_montage for task {task_id}: {e}", exc_info=True)
        # Cleanup partial directories if setup fails
        task_temp_dir = os.path.join(app.config['UPLOAD_FOLDER'], task_id)
        if os.path.exists(task_temp_dir):
            shutil.rmtree(task_temp_dir, ignore_errors=True)
        return jsonify({"error": f"A server error occurred: {e}"}), 500


@app.route('/status/<task_id>')
def task_status(task_id):
    task = tasks.get(task_id)
    return jsonify(task) if task else (jsonify({"status": "error", "message": "Task not found."}), 404)

@app.route('/download/<task_id>/<filename>')
def download_file(task_id, filename):
    directory = os.path.join(app.config['OUTPUT_FOLDER'], task_id)
    return send_from_directory(directory, secure_filename(filename), as_attachment=True)

@app.route('/stream/<task_id>/<filename>')
def stream_video(task_id, filename):
    directory = os.path.join(app.config['OUTPUT_FOLDER'], task_id)
    return send_from_directory(directory, secure_filename(filename), mimetype='video/mp4')

if __name__ == '__main__':
    app.logger.info(f"Persistent video storage is at: {os.path.abspath(SERVER_VIDEOS_FOLDER)}")
    app.run(debug=True, host='0.0.0.0', port=5000)