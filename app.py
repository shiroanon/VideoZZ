# app.py
from flask import Flask, request, jsonify, render_template, send_from_directory, Response, stream_with_context
import os
import uuid
import threading
import shutil
import subprocess # For yt-dlp
import logging # For better logging
from werkzeug.utils import secure_filename
# Import your montage creation script
import montage_maker

# Configuration
UPLOAD_FOLDER = 'uploads'
OUTPUT_FOLDER = 'outputs'
YT_DOWNLOADS_FOLDER = os.path.join(UPLOAD_FOLDER, 'yt_downloads') # Subfolder for yt-dlp downloads

ALLOWED_EXTENSIONS_VIDEO = {'mp4', 'mov', 'avi', 'mkv', 'webm'}
ALLOWED_EXTENSIONS_AUDIO = {'mp3', 'wav', 'aac', 'ogg', 'flac'}
ALLOWED_EXTENSIONS_LABELS = {'txt'}

# YT-DLP Configuration (Optional: if yt-dlp is not in PATH)
# YT_DLP_PATH = "/path/to/your/yt-dlp" # Or None if it's in PATH
YT_DLP_PATH = None # Assume it's in PATH by default

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['OUTPUT_FOLDER'] = OUTPUT_FOLDER
app.config['YT_DOWNLOADS_FOLDER'] = YT_DOWNLOADS_FOLDER # Store for easy access
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024 * 1024  # 2 GB limit, adjust as needed

# Ensure directories exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)
os.makedirs(YT_DOWNLOADS_FOLDER, exist_ok=True)

# Setup basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
app.logger.handlers.extend(logging.getLogger().handlers) # Use root logger's handlers for Flask too


# In-memory store for task statuses
tasks = {}

def allowed_file(filename, allowed_extensions):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed_extensions

def update_task_progress(task_id, progress_data):
    if task_id in tasks:
        tasks[task_id].update(progress_data)
        # Add a timestamp for the last update to help with cleanup of old tasks
        tasks[task_id]['last_updated'] = logging.Formatter().formatTime(logging.LogRecord(None,None,"",0,"", (), None, None), "%Y-%m-%d %H:%M:%S")
    app.logger.info(f"Task {task_id} Progress: {progress_data}")


def run_yt_dlp_download(video_url, download_path, task_id_for_progress=None):
    """Downloads a video using yt-dlp."""
    if not montage_maker.check_command_exists("yt-dlp") and not (YT_DLP_PATH and os.path.exists(YT_DLP_PATH)):
        msg = "yt-dlp command not found. Please install it or configure YT_DLP_PATH."
        if task_id_for_progress: update_task_progress(task_id_for_progress, {"status": "error", "message": msg})
        raise montage_maker.MontageError(msg)

    yt_dlp_executable = YT_DLP_PATH if YT_DLP_PATH and os.path.exists(YT_DLP_PATH) else "yt-dlp"
    
    # Create a unique filename for the download to avoid collisions if multiple users download same video
    # However, for simplicity, we'll just use a task-specific subfolder.
    # We want the actual filename yt-dlp chooses, so we specify output template to the directory.
    # yt-dlp will create a file like 'Video Title [id].mp4'
    # It's better to let yt-dlp name the file and then find it.
    
    # Best format, preferring mp4, up to 1080p to keep size manageable for processing
    # -S res:1080 means sort by resolution, take best up to 1080p. Add ext:mp4 for mp4 preference.
    # Using -f "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4][height<=1080]/best"
    # For simplicity for now: best video and audio, let yt-dlp merge, prefer mp4.
    cmd = [
        yt_dlp_executable,
        '-f', 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best', # Prefer mp4, then best
        '--merge-output-format', 'mp4', # Ensure output is mp4 if merging is needed
        '-o', os.path.join(download_path, '%(title)s.%(ext)s'), # Output to the specified path with original title
        video_url
    ]
    
    app.logger.info(f"Executing yt-dlp: {' '.join(cmd)}")
    if task_id_for_progress: update_task_progress(task_id_for_progress, {"status": "processing", "message": f"Downloading video from URL..."})

    try:
        process = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if process.returncode != 0:
            error_message = f"yt-dlp error (code {process.returncode}): {process.stderr}"
            app.logger.error(error_message)
            if task_id_for_progress: update_task_progress(task_id_for_progress, {"status": "error", "message": f"Failed to download video. {process.stderr[:200]}..."})
            raise montage_maker.MontageError(f"yt-dlp failed: {process.stderr}")
        
        # Find the downloaded file. yt-dlp usually outputs one video file.
        # This is a bit naive; yt-dlp might download multiple files (e.g., thumbnail, info.json)
        # or if the output template changes. A more robust way is to parse yt-dlp's --print filename output.
        # For now, assume one video file (mp4 ideally).
        downloaded_files = [f for f in os.listdir(download_path) if os.path.isfile(os.path.join(download_path, f))]
        video_files = [f for f in downloaded_files if f.lower().endswith(('.mp4', '.mkv', '.webm'))] # Add other formats if needed

        if not video_files:
            app.logger.error(f"yt-dlp ran, but no video file found in {download_path}. Files: {downloaded_files}")
            if task_id_for_progress: update_task_progress(task_id_for_progress, {"status": "error", "message": "Download complete, but video file not found."})
            raise montage_maker.MontageError("yt-dlp downloaded, but no video file found.")
        
        # Prefer mp4 if multiple video formats exist (shouldn't happen with our command)
        final_video_file = video_files[0] # Take the first one
        for vf in video_files:
            if vf.lower().endswith('.mp4'):
                final_video_file = vf
                break
        
        full_video_path = os.path.join(download_path, final_video_file)
        app.logger.info(f"yt-dlp downloaded: {full_video_path}")
        if task_id_for_progress: update_task_progress(task_id_for_progress, {"status": "processing", "message": f"Video '{final_video_file}' downloaded successfully."})
        return full_video_path

    except FileNotFoundError: # yt-dlp executable not found
        app.logger.error("yt-dlp command not found during execution.")
        if task_id_for_progress: update_task_progress(task_id_for_progress, {"status": "error", "message": "yt-dlp executable not found."})
        raise montage_maker.MontageError("yt-dlp command not found.")
    except Exception as e:
        app.logger.error(f"Unexpected error during yt-dlp download: {e}", exc_info=True)
        if task_id_for_progress: update_task_progress(task_id_for_progress, {"status": "error", "message": f"Video download error: {e}"})
        raise montage_maker.MontageError(f"yt-dlp download error: {e}")


def process_montage_task(task_id, input_video_path, label_file_path, output_video_name, options):
    # ... (existing setup) ...
    try:
        tasks[task_id]['status'] = 'processing'
        tasks[task_id]['message'] = 'Starting montage creation...'
        update_task_progress(task_id, {"status": "processing", "message": "Preparing for montage generation..."})


        output_dir_for_task = os.path.join(app.config['OUTPUT_FOLDER'], task_id)
        os.makedirs(output_dir_for_task, exist_ok=True)
        output_video_full_path = os.path.join(output_dir_for_task, output_video_name)

        final_output_path = montage_maker.create_rhythmic_montage_ffmpeg(
            input_video_path=input_video_path,
            output_video_path=output_video_full_path,
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
        
        if final_output_path:
            # update_task_progress with 'completed' is called from within montage_maker now
            # tasks[task_id]['output_file'] = os.path.basename(final_output_path)
            # tasks[task_id]['status'] = 'completed'
            # tasks[task_id]['message'] = f"Montage '{os.path.basename(final_output_path)}' created successfully."
            pass # Callback handles this
        else: # Should be caught by exceptions
            tasks[task_id]['status'] = 'error'
            tasks[task_id]['message'] = 'Montage process finished but no output path was confirmed by the process.'
            update_task_progress(task_id, {"status":"error", "message": tasks[task_id]['message']})


    except montage_maker.MontageError as e:
        app.logger.error(f"MontageError in task {task_id}: {e}")
        tasks[task_id]['status'] = 'error'
        tasks[task_id]['message'] = str(e)
        # The callback inside montage_maker should have already set this for errors originating there.
        # If the error is raised *before* calling montage_maker (e.g., yt-dlp error), this sets it.
        # update_task_progress(task_id, {"status":"error", "message": str(e)}) # Ensure it's updated if not already
    except Exception as e:
        app.logger.error(f"Unexpected error in task {task_id}: {e}", exc_info=True)
        tasks[task_id]['status'] = 'error'
        tasks[task_id]['message'] = f"An unexpected server error occurred during processing: {str(e)}"
        update_task_progress(task_id, {"status":"error", "message": tasks[task_id]['message']})
    finally:
        # Cleanup:
        # 1. Uploaded label file
        # 2. Uploaded external audio file (if any)
        # 3. Input video (whether uploaded or yt-dlp downloaded)
        # Input video path is either from UPLOAD_FOLDER/task_id or YT_DOWNLOADS_FOLDER/task_id
        
        files_to_delete_dirs = []
        if label_file_path and os.path.exists(label_file_path):
            try: 
                os.remove(label_file_path)
                files_to_delete_dirs.append(os.path.dirname(label_file_path))
            except OSError as e: app.logger.warning(f"Could not remove temp label file {label_file_path}: {e}")

        if options.get('audio_file_path') and os.path.exists(options['audio_file_path']):
            try:
                os.remove(options['audio_file_path'])
                files_to_delete_dirs.append(os.path.dirname(options['audio_file_path']))
            except OSError as e: app.logger.warning(f"Could not remove temp audio file {options['audio_file_path']}: {e}")
        
        # Clean up the input video (uploaded or ytdl)
        # The input_video_path's directory (task_id specific subfolder) should be cleaned
        if input_video_path and os.path.exists(input_video_path):
            try:
                # os.remove(input_video_path) # Removing the whole directory is better
                input_video_dir = os.path.dirname(input_video_path)
                if input_video_dir not in files_to_delete_dirs: # Avoid duplicate rmtree calls
                    files_to_delete_dirs.append(input_video_dir)
            except OSError as e: app.logger.warning(f"Error preparing input video for cleanup {input_video_path}: {e}")

        for dir_path in set(files_to_delete_dirs): # Use set to avoid duplicates
            if dir_path and os.path.exists(dir_path):
                try:
                    shutil.rmtree(dir_path)
                    app.logger.info(f"Cleaned up temporary directory: {dir_path}")
                except OSError as e:
                    app.logger.warning(f"Could not remove temp directory {dir_path}: {e}")


@app.route('/')
def index():
    return render_template('index.html')

@app.route('/create_montage', methods=['POST'])
def create_montage_route():
    try:
        task_id = str(uuid.uuid4())
        
        # Determine input video source: URL or File Upload
        input_video_url = request.form.get('input_video_url')
        input_video_file = request.files.get('input_video_file') # Changed name for clarity

        if not input_video_url and not input_video_file:
            return jsonify({"error": "Missing input video: Please provide a URL or upload a file."}), 400
        if input_video_url and input_video_file and input_video_file.filename != '':
             return jsonify({"error": "Ambiguous input: Please provide EITHER a video URL OR an uploaded file, not both."}), 400


        # --- Label File Handling (Required) ---
        if 'label_file' not in request.files:
            return jsonify({"error": "Missing label file."}), 400
        label_file = request.files['label_file']
        if not label_file or label_file.filename == '':
            return jsonify({"error": "No label file selected."}), 400
        if not allowed_file(label_file.filename, ALLOWED_EXTENSIONS_LABELS):
            return jsonify({"error": f"Invalid label file type. Allowed: {', '.join(ALLOWED_EXTENSIONS_LABELS)}"}), 400
        
        # --- Save Label File ---
        # Each task gets its own subfolder in 'uploads' for its transient files
        task_specific_upload_dir = os.path.join(app.config['UPLOAD_FOLDER'], task_id)
        os.makedirs(task_specific_upload_dir, exist_ok=True)
        
        label_filename = secure_filename(label_file.filename)
        label_file_path = os.path.join(task_specific_upload_dir, label_filename)
        label_file.save(label_file_path)

        # --- Input Video Processing ---
        input_video_path = None # This will be the path to the local video file for ffmpeg

        if input_video_url:
            app.logger.info(f"Task {task_id}: Processing video URL: {input_video_url}")
            # Download the video using yt-dlp
            # yt-dlp downloads will go into YT_DOWNLOADS_FOLDER/task_id
            yt_task_download_dir = os.path.join(app.config['YT_DOWNLOADS_FOLDER'], task_id)
            os.makedirs(yt_task_download_dir, exist_ok=True)
            try:
                # Pass task_id so yt-dlp can update progress directly if it's a long download
                input_video_path = run_yt_dlp_download(input_video_url, yt_task_download_dir, task_id)
            except montage_maker.MontageError as e: # Catch errors from yt-dlp
                app.logger.error(f"Task {task_id}: yt-dlp download failed: {e}")
                # Clean up partially created task dir for yt-dlp if it exists and is empty
                if os.path.exists(yt_task_download_dir) and not os.listdir(yt_task_download_dir):
                    shutil.rmtree(yt_task_download_dir)
                # Clean up uploaded label file as well
                if os.path.exists(label_file_path): os.remove(label_file_path)
                if os.path.exists(task_specific_upload_dir) and not os.listdir(task_specific_upload_dir):
                     shutil.rmtree(task_specific_upload_dir)
                return jsonify({"error": f"Video download failed: {str(e)}"}), 400 # Or 500 for server-side tool failure

        elif input_video_file and input_video_file.filename != '':
            app.logger.info(f"Task {task_id}: Processing uploaded video file: {input_video_file.filename}")
            if not allowed_file(input_video_file.filename, ALLOWED_EXTENSIONS_VIDEO):
                # Clean up uploaded label file
                if os.path.exists(label_file_path): os.remove(label_file_path)
                if os.path.exists(task_specific_upload_dir) and not os.listdir(task_specific_upload_dir):
                     shutil.rmtree(task_specific_upload_dir)
                return jsonify({"error": f"Invalid input video file type. Allowed: {', '.join(ALLOWED_EXTENSIONS_VIDEO)}"}), 400
            
            input_video_filename = secure_filename(input_video_file.filename)
            input_video_path = os.path.join(task_specific_upload_dir, input_video_filename) # Save to task-specific upload dir
            input_video_file.save(input_video_path)
        else: # Should be caught by initial check, but as a safeguard
             return jsonify({"error": "No video source provided."}),400


        # --- Optional External Audio File ---
        audio_file_path = None
        audio_file = request.files.get('audio_file')
        if audio_file and audio_file.filename != '':
            if not allowed_file(audio_file.filename, ALLOWED_EXTENSIONS_AUDIO):
                # Clean up already saved files if audio validation fails
                if os.path.exists(label_file_path): os.remove(label_file_path)
                if input_video_path and os.path.exists(input_video_path):
                    # If it was a ytdl download, its folder might need specific cleanup
                    if input_video_url and os.path.dirname(input_video_path) == os.path.join(app.config['YT_DOWNLOADS_FOLDER'], task_id):
                        shutil.rmtree(os.path.dirname(input_video_path))
                    # If it was an upload, its folder
                    elif os.path.dirname(input_video_path) == task_specific_upload_dir:
                        os.remove(input_video_path) # Remove just the file for now, dir cleaned later if empty
                # Clean up the task_specific_upload_dir if it's now empty (e.g. only label was there)
                if os.path.exists(task_specific_upload_dir) and not os.listdir(task_specific_upload_dir):
                     shutil.rmtree(task_specific_upload_dir)

                return jsonify({"error": f"Invalid audio file type. Allowed: {', '.join(ALLOWED_EXTENSIONS_AUDIO)}"}), 400
            
            audio_filename = secure_filename(audio_file.filename)
            audio_file_path = os.path.join(task_specific_upload_dir, audio_filename) # Also save to task-specific dir
            audio_file.save(audio_file_path)

        # --- Form Options ---
        options = {
            "resolution": request.form.get('resolution', '1280x720'),
            "total_duration": float(request.form.get('total_duration', 0)),
            "total_scenes": int(request.form.get('total_scenes', 0)),
            "min_scene_duration": float(request.form.get('min_scene_duration', montage_maker.DEFAULT_MIN_BEAT_GROUPED_SCENE_DURATION)),
            "audio_mode": request.form.get('audio_mode', 'replace'),
            "audio_file_path": audio_file_path
        }

        output_video_name = f"montage_output_{task_id}.mp4"
        
        tasks[task_id] = {
            "status": "queued", 
            "message": "Task queued for processing.",
            "output_file": None,
            "task_id": task_id # Useful for client side to have it in status too
        }
        update_task_progress(task_id, tasks[task_id]) # Initial progress update

        thread = threading.Thread(target=process_montage_task, args=(
            task_id, input_video_path, label_file_path, output_video_name, options
        ))
        thread.start()

        return jsonify({"task_id": task_id, "message": "Processing successfully initiated..."}), 202

    except ValueError as e:
        app.logger.error(f"Invalid form parameter value in /create_montage: {e}", exc_info=True)
        return jsonify({"error": f"Invalid value for a form parameter: {e}"}), 400
    except montage_maker.MontageError as e: # Catch errors explicitly raised by yt-dlp or other setup
        app.logger.error(f"Setup MontageError in /create_montage: {e}", exc_info=False)
        return jsonify({"error": str(e)}), 400 # Or 500 if it's a tool failure
    except Exception as e:
        app.logger.error(f"Unexpected error in /create_montage before task start: {e}", exc_info=True)
        return jsonify({"error": f"An unexpected server error occurred during request setup: {str(e)}"}), 500


@app.route('/status/<task_id>')
def task_status(task_id):
    task = tasks.get(task_id)
    if not task:
        return jsonify({"status": "error", "message": "Task not found or already cleaned up.", "task_id": task_id}), 404
    return jsonify(task)

@app.route('/download/<task_id>/<filename>')
def download_file(task_id, filename):
    s_filename = secure_filename(filename)
    if s_filename != filename:
        app.logger.warning(f"Download attempt with potentially unsafe filename: original='{filename}', secured='{s_filename}' for task {task_id}")
        return jsonify({"error": "Invalid filename."}), 400

    directory = os.path.join(app.config['OUTPUT_FOLDER'], task_id)
    file_path = os.path.join(directory, s_filename)

    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        app.logger.error(f"Download failed: File not found at {file_path} for task {task_id}")
        return jsonify({"error": "Output file not found. Task might have failed, is processing, or file was cleaned up."}), 404
        
    return send_from_directory(directory, s_filename, as_attachment=True)

# New endpoint to stream/serve the video for the <video> tag
@app.route('/stream/<task_id>/<filename>')
def stream_video(task_id, filename):
    s_filename = secure_filename(filename)
    if s_filename != filename:
        return jsonify({"error": "Invalid filename."}), 400

    directory = os.path.join(app.config['OUTPUT_FOLDER'], task_id)
    file_path = os.path.join(directory, s_filename)

    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        return jsonify({"error": "Video file not found for streaming."}), 404

    # Simple send_from_directory works for most modern browsers for <video> source
    # For true streaming (e.g., handling Range requests for seeking), it's more complex.
    # Flask's send_from_directory handles conditional GETs and byte ranges sufficiently for many cases.
    return send_from_directory(directory, s_filename, mimetype='video/mp4')


if __name__ == '__main__':
    # Check for essential commands
    essential_commands = {"ffmpeg": False, "ffprobe": False, "yt-dlp": False}
    for cmd_name in essential_commands:
        if montage_maker.check_command_exists(cmd_name) or \
           (cmd_name == "yt-dlp" and YT_DLP_PATH and os.path.exists(YT_DLP_PATH)):
            essential_commands[cmd_name] = True
            app.logger.info(f"{cmd_name} found.")
        else:
            app.logger.error(f"CRITICAL ERROR: {cmd_name} not found in system PATH " + 
                             (f"or at configured YT_DLP_PATH ('{YT_DLP_PATH}') for yt-dlp. " if cmd_name == "yt-dlp" and YT_DLP_PATH else "") +
                             "Please install it and ensure it is accessible.")

    if not all(essential_commands.values()):
        app.logger.error("One or more essential tools are missing. The application might not function correctly. Exiting.")
        # exit(1) # Or allow to run with warnings

    app.logger.info("Flask application starting...")
    app.run(debug=True, host='0.0.0.0', port=5000) # use_reloader=False if threading causes issues with it