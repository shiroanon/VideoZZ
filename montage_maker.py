# montage_maker.py
import subprocess
import random
import os
import json
import tempfile
import shutil # For shutil.which

DEFAULT_MIN_BEAT_GROUPED_SCENE_DURATION = 0.6
MIN_FINAL_SCENE_WARN = 0.1

class MontageError(Exception):
    """Custom exception for montage errors."""
    pass

def check_command_exists(command_name):
    return shutil.which(command_name) is not None

def get_video_duration(video_path, progress_callback=None, task_id=None):
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", video_path]
    try:
        process = subprocess.run(cmd, capture_output=True, text=True, check=True)
        metadata = json.loads(process.stdout)
        if 'format' in metadata and 'duration' in metadata['format']:
            return float(metadata['format']['duration'])
        if progress_callback and task_id:
            progress_callback(task_id, {"status": "error", "message": f"Error: Could not retrieve duration from ffprobe output for '{os.path.basename(video_path)}'."})
        raise MontageError(f"Error: Could not retrieve duration from ffprobe output for '{os.path.basename(video_path)}'.")
    except subprocess.CalledProcessError as e:
        if progress_callback and task_id:
            progress_callback(task_id, {"status": "error", "message": f"ffprobe error for '{os.path.basename(video_path)}': {e.stderr}"})
        raise MontageError(f"ffprobe error for '{os.path.basename(video_path)}': {e.stderr}")
    except Exception as e:
        if progress_callback and task_id:
            progress_callback(task_id, {"status": "error", "message": f"Error getting video duration for '{os.path.basename(video_path)}': {e}"})
        raise MontageError(f"Error getting video duration for '{os.path.basename(video_path)}': {e}")


def run_ffmpeg_command(cmd_list, operation_desc, progress_callback=None, task_id=None):
    if progress_callback and task_id:
        progress_callback(task_id, {"status": "processing", "message": f"Executing FFmpeg for {operation_desc}..."})
    # print(f"Executing FFmpeg for {operation_desc}: {' '.join(cmd_list)}") # Keep for server log
    try:
        process = subprocess.run(cmd_list, capture_output=True, text=True, check=False) # check=False to handle errors manually
        if process.returncode != 0:
            error_message = f"Error during {operation_desc}:\nCommand: {' '.join(cmd_list)}\nReturn code: {process.returncode}\nSTDOUT:\n{process.stdout}\nSTDERR:\n{process.stderr}"
            if progress_callback and task_id:
                progress_callback(task_id, {"status": "error", "message": f"Error during {operation_desc}. Check server logs."})
            raise MontageError(error_message)
        return True
    except FileNotFoundError as e: # Handle case where ffmpeg itself is not found
        error_message = f"FFmpeg command not found: {cmd_list[0]}. Ensure FFmpeg is installed and in PATH. Error: {e}"
        if progress_callback and task_id:
            progress_callback(task_id, {"status": "error", "message": error_message})
        raise MontageError(error_message)
    except Exception as e:
        error_message = f"An unexpected error occurred while running ffmpeg for {operation_desc}: {e}"
        if progress_callback and task_id:
            progress_callback(task_id, {"status": "error", "message": f"Unexpected error during {operation_desc}. Check server logs."})
        raise MontageError(error_message)

def parse_audacity_labels(label_file_path, progress_callback=None, task_id=None):
    beat_timestamps = set()
    if not os.path.exists(label_file_path):
        if progress_callback and task_id:
            progress_callback(task_id, {"status": "error", "message": f"Error: Label file not found at '{os.path.basename(label_file_path)}'"})
        raise MontageError(f"Error: Label file not found at '{label_file_path}'")
    try:
        with open(label_file_path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line: continue
                parts = line.split('\t')
                if len(parts) >= 1:
                    try:
                        beat_timestamps.add(float(parts[0]))
                    except ValueError:
                        if progress_callback and task_id: # Log warning, but don't stop
                            progress_callback(task_id, {"status": "processing", "message": f"Warning: Could not parse start time from label line: '{line}'"})
        parsed_timestamps = sorted(list(beat_timestamps))
        if not parsed_timestamps and progress_callback and task_id:
             progress_callback(task_id, {"status": "processing", "message": "Warning: No valid beat timestamps found in label file."})
        return parsed_timestamps
    except Exception as e:
        if progress_callback and task_id:
            progress_callback(task_id, {"status": "error", "message": f"Error reading or parsing label file '{os.path.basename(label_file_path)}': {e}"})
        raise MontageError(f"Error reading or parsing label file '{label_file_path}': {e}")
def check_command_exists(command_name): # Already exists, ensure it's used
    return shutil.which(command_name) is not None

def generate_beat_grouped_scene_durations(
    beat_timestamps,
    start_processing_from_beat_index,
    min_grouped_duration,
    progress_callback=None, task_id=None
):
    grouped_scene_durations = []
    if not beat_timestamps or start_processing_from_beat_index >= len(beat_timestamps) - 1:
        return []

    current_interval_start_beat_idx = start_processing_from_beat_index
    while current_interval_start_beat_idx < len(beat_timestamps) - 1:
        group_start_ts = beat_timestamps[current_interval_start_beat_idx]
        accumulated_duration_for_this_scene = 0.0
        current_interval_end_beat_idx = current_interval_start_beat_idx
        while current_interval_end_beat_idx < len(beat_timestamps) - 1:
            next_beat_ts = beat_timestamps[current_interval_end_beat_idx + 1]
            current_group_duration = next_beat_ts - group_start_ts
            if current_group_duration <=0:
                 current_interval_end_beat_idx +=1
                 if current_interval_end_beat_idx >= len(beat_timestamps)-1: break
                 # group_start_ts = beat_timestamps[current_interval_end_beat_idx] # This was a bug if next_beat_ts - group_start_ts <= 0 because group_start_ts wasn't updated
                 if current_interval_end_beat_idx < len(beat_timestamps): # ensure index is valid
                     group_start_ts = beat_timestamps[current_interval_end_beat_idx]
                 else: # reached end of beats, break inner loop
                     break
                 continue
            if current_group_duration >= min_grouped_duration:
                accumulated_duration_for_this_scene = current_group_duration
                current_interval_end_beat_idx += 1; break
            if current_interval_end_beat_idx + 1 == len(beat_timestamps) - 1: # If this is the second to last beat
                accumulated_duration_for_this_scene = current_group_duration
                current_interval_end_beat_idx += 1; break # Include the last interval
            current_interval_end_beat_idx += 1

        if accumulated_duration_for_this_scene > 0:
            grouped_scene_durations.append(accumulated_duration_for_this_scene)
        current_interval_start_beat_idx = current_interval_end_beat_idx
        if current_interval_start_beat_idx >= len(beat_timestamps) -1 : break
    return grouped_scene_durations

def create_rhythmic_montage_ffmpeg(
    input_video_path, output_video_path, label_file_path,
    output_resolution_str, target_total_output_duration_sec, target_total_num_scenes,
    min_beat_grouped_scene_duration, audio_file_path=None, audio_mix_behavior="replace",
    progress_callback=None, task_id=None # Added for progress
):
    # --- 0. Checks ---
    if not all(check_command_exists(cmd) for cmd in ["ffmpeg", "ffprobe"]):
        if progress_callback and task_id:
            progress_callback(task_id, {"status": "error", "message": "Error: ffmpeg or ffprobe not found. Ensure they are installed and in your system PATH."})
        raise MontageError("ffmpeg or ffprobe not found.")
    # Removed random.py check as it's not relevant for module usage

    # --- 1. Validate Inputs & Get Video Info ---
    if not os.path.exists(input_video_path):
        if progress_callback and task_id:
            progress_callback(task_id, {"status": "error", "message": f"Error: Input video file not found: '{os.path.basename(input_video_path)}'"})
        raise MontageError(f"Input video file not found: '{input_video_path}'")
    if audio_file_path and not os.path.exists(audio_file_path):
        if progress_callback and task_id:
            progress_callback(task_id, {"status": "error", "message": f"Error: Audio file not found: '{os.path.basename(audio_file_path)}'"})
        raise MontageError(f"Audio file not found: '{audio_file_path}'")
    if audio_file_path and audio_mix_behavior not in ["replace", "mix"]:
        if progress_callback and task_id:
            progress_callback(task_id, {"status": "error", "message": f"Error: Invalid audio_mix_behavior '{audio_mix_behavior}'. Must be 'replace' or 'mix'."})
        raise MontageError(f"Invalid audio_mix_behavior '{audio_mix_behavior}'. Must be 'replace' or 'mix'.")

    try:
        out_w_str, out_h_str = output_resolution_str.lower().split('x')
        output_width, output_height = int(out_w_str), int(out_h_str)
        if output_width <= 0 or output_height <= 0: raise ValueError
    except ValueError:
        if progress_callback and task_id:
            progress_callback(task_id, {"status": "error", "message": "Error: Invalid output resolution format. Use WxH (e.g., 1280x720)." })
        raise MontageError("Invalid output resolution format. Use WxH.")

    if progress_callback and task_id:
        progress_callback(task_id, {"status": "processing", "message": f"Getting duration of input video: {os.path.basename(input_video_path)}..."})
    input_video_duration = get_video_duration(input_video_path, progress_callback, task_id)
    if input_video_duration is None: return # Error handled in get_video_duration
    if progress_callback and task_id:
        progress_callback(task_id, {"status": "processing", "message": f"Input video duration: {input_video_duration:.2f} seconds"})

    # --- 2. Parse Audacity Labels ---
    if progress_callback and task_id:
        progress_callback(task_id, {"status": "processing", "message": f"Parsing Audacity labels: {os.path.basename(label_file_path)}..."})
    all_beat_timestamps = parse_audacity_labels(label_file_path, progress_callback, task_id)
    if all_beat_timestamps is None: return # Error handled in parse_audacity_labels

    # --- 3. Plan the Output Structure ---
    temp_scene_files_for_concat = []
    actual_total_output_duration = 0.0
    initial_scene_actual_duration = 0.0

    audio_opts_for_temp_clips = ["-c:a", "aac", "-b:a", "128k", "-ar", "44100"] # Added sample rate

    # Use a temporary directory that will be cleaned up
    with tempfile.TemporaryDirectory(prefix="rhythmic_montage_") as temp_dir:
        if progress_callback and task_id:
            progress_callback(task_id, {"status": "processing", "message": f"Created temporary directory: {temp_dir}"})

        # --- 3a. Determine and Create Initial Scene (up to first beat) ---
        if not all_beat_timestamps:
            if progress_callback and task_id:
                progress_callback(task_id, {"status": "processing", "message": "Warning: Label file empty or no valid beats. Output might be a single random clip."})
            initial_scene_duration_from_beats = target_total_output_duration_sec if target_total_output_duration_sec > 0 else 5.0
            if initial_scene_duration_from_beats <=0 : initial_scene_duration_from_beats = 5.0
        else:
            initial_scene_duration_from_beats = all_beat_timestamps[0]
            if initial_scene_duration_from_beats <= 0: # If first beat is at 0.0 or negative
                if target_total_output_duration_sec <= 0 and not (len(all_beat_timestamps) > 1 and target_total_num_scenes > 1) :
                    # If no target duration/scenes, and this is potentially the only scene
                    initial_scene_duration_from_beats = MIN_FINAL_SCENE_WARN # Small positive duration
                    if progress_callback and task_id:
                        progress_callback(task_id, {"status": "processing", "message": f"First beat at/before 0s. Setting initial scene to {initial_scene_duration_from_beats}s."})
                else: # Other scenes will follow, so this initial scene can be 0
                     initial_scene_duration_from_beats = 0
                     if progress_callback and task_id:
                        progress_callback(task_id, {"status": "processing", "message": "First beat at 0s. No separate initial scene before first beat."})

        if initial_scene_duration_from_beats > 0:
            initial_scene_actual_duration = min(input_video_duration, initial_scene_duration_from_beats)
            if input_video_duration <= initial_scene_actual_duration : # Video too short for this segment
                random_start_for_initial = 0
                initial_scene_actual_duration = input_video_duration # Use full video if it's shorter than desired
            else:
                 random_start_for_initial = random.uniform(0, input_video_duration - initial_scene_actual_duration)

            initial_clip_path = os.path.join(temp_dir, "initial_scene_000.mp4")
            ffmpeg_cmd_initial = [
                "ffmpeg", "-ss", str(random_start_for_initial), "-i", input_video_path,
                "-t", str(initial_scene_actual_duration),
                "-vf", f"scale={output_width}:{output_height}:force_original_aspect_ratio=decrease,pad={output_width}:{output_height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23"
            ] + audio_opts_for_temp_clips + ["-threads", "0", "-y", initial_clip_path]

            if run_ffmpeg_command(ffmpeg_cmd_initial, "extracting initial scene", progress_callback, task_id):
                temp_scene_files_for_concat.append(initial_clip_path)
                actual_total_output_duration += initial_scene_actual_duration
            else: initial_scene_actual_duration = 0 # Failed to create

        # --- 3b. Generate and Select Beat-Grouped Scene Durations for Subsequent Part ---
        beat_grouped_durations_for_rest = []
        if len(all_beat_timestamps) > 1: # Need at least two beats to form an interval
            # Determine start_index for grouping: if initial scene was based on first beat, start from 1st beat (index 0).
            # If initial scene was >0 (i.e. beat_timestamps[0] > 0), then we still process from beat 0.
            # generate_beat_grouped_scene_durations handles the intervals *between* beats.
            start_beat_idx_for_grouping = 0
            beat_grouped_durations_for_rest = generate_beat_grouped_scene_durations(
                all_beat_timestamps, start_beat_idx_for_grouping, min_beat_grouped_scene_duration,
                progress_callback, task_id
            )

        selected_beat_scene_durations = []
        target_duration_for_beat_part = max(0, target_total_output_duration_sec - actual_total_output_duration) if target_total_output_duration_sec > 0 else float('inf') # Effectively no limit if target_total_output_duration_sec is 0
        num_beat_scenes_to_select = max(0, target_total_num_scenes - (1 if initial_scene_actual_duration > 0 else 0)) if target_total_num_scenes > 0 else float('inf')

        if beat_grouped_durations_for_rest:
            current_beat_part_duration = 0.0
            scenes_selected_count = 0
            for i, dur in enumerate(beat_grouped_durations_for_rest):
                if scenes_selected_count >= num_beat_scenes_to_select:
                    break
                if target_total_output_duration_sec > 0: # Only apply duration limit if one is set
                    if current_beat_part_duration + dur <= target_duration_for_beat_part:
                        selected_beat_scene_durations.append(dur)
                        current_beat_part_duration += dur
                        scenes_selected_count += 1
                    else: # Adding full `dur` would exceed target
                        remaining_time = target_duration_for_beat_part - current_beat_part_duration
                        if remaining_time > MIN_FINAL_SCENE_WARN: # Add a final truncated scene
                            selected_beat_scene_durations.append(remaining_time)
                            scenes_selected_count += 1
                        break # Reached target duration for beat part
                else: # No target duration, add based on scene count or all available
                    selected_beat_scene_durations.append(dur)
                    scenes_selected_count += 1
        
        if selected_beat_scene_durations:
            if progress_callback and task_id:
                progress_callback(task_id, {"status": "processing", "message": f"Selected {len(selected_beat_scene_durations)} subsequent beat-synced scenes."})
            for i, scene_duration in enumerate(selected_beat_scene_durations):
                if scene_duration <=0: continue # Skip zero or negative duration scenes
                scene_num_display = i + 1 + (1 if initial_scene_actual_duration > 0 else 0) # For user message

                if input_video_duration < scene_duration:
                    if progress_callback and task_id:
                         progress_callback(task_id, {"status": "processing", "message": f"Warning: Scene {scene_num_display} duration ({scene_duration:.2f}s) is longer than input video ({input_video_duration:.2f}s). Skipping this scene."})
                    continue # Skip if source video is too short for this scene

                max_start_time = input_video_duration - scene_duration
                random_start_in_visual_src = random.uniform(0, max_start_time if max_start_time > 0 else 0)
                beat_scene_path = os.path.join(temp_dir, f"beat_scene_{i:03d}.mp4")

                ffmpeg_cmd_beat = [
                    "ffmpeg", "-ss", str(random_start_in_visual_src), "-i", input_video_path,
                    "-t", str(scene_duration),
                    "-vf", f"scale={output_width}:{output_height}:force_original_aspect_ratio=decrease,pad={output_width}:{output_height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23"
                ] + audio_opts_for_temp_clips + ["-threads", "0", "-y", beat_scene_path]
                if run_ffmpeg_command(ffmpeg_cmd_beat, f"extracting beat-synced scene {scene_num_display}", progress_callback, task_id):
                    temp_scene_files_for_concat.append(beat_scene_path)
                    actual_total_output_duration += scene_duration
                else: pass # Error handled by run_ffmpeg_command

        # --- 4. Concatenate All Clips & Optionally Add External Audio ---
        if not temp_scene_files_for_concat:
            if progress_callback and task_id:
                progress_callback(task_id, {"status": "error", "message": "Error: No video clips were generated. This could be due to very short input video or restrictive parameters."})
            raise MontageError("No video clips generated.")

        concat_list_path = os.path.join(temp_dir, "concat_list.txt")
        with open(concat_list_path, 'w') as f:
            for p in temp_scene_files_for_concat: f.write(f"file '{os.path.relpath(p, temp_dir)}'\n") # Use relative paths for concat list

        ffmpeg_final_cmd = ["ffmpeg", "-f", "concat", "-safe", "0", "-i", concat_list_path]
        
        # Make sure output directory exists
        os.makedirs(os.path.dirname(output_video_path), exist_ok=True)

        if audio_file_path:
            ffmpeg_final_cmd.extend(["-i", audio_file_path]) # Add external audio as second input
            if audio_mix_behavior == "mix":
                if progress_callback and task_id:
                    progress_callback(task_id, {"status": "processing", "message": f"Mixing concatenated video audio with external audio: {os.path.basename(audio_file_path)}"})
                # Input 0 is concat video, Input 1 is external audio
                # [0:a] is audio from concat, [1:a] is audio from external
                ffmpeg_final_cmd.extend([
                    "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=first:dropout_transition=3[aout]", # Mix audio
                    "-map", "0:v:0",      # Video from concat (input 0)
                    "-map", "[aout]"      # Mixed audio output
                ])
                ffmpeg_final_cmd.extend(["-c:v", "libx264", "-preset", "ultrafast", "-crf", "22"]) # Re-encode video for good quality
                ffmpeg_final_cmd.extend(["-c:a", "aac", "-b:a", "192k", "-ar", "44100"]) # Encode mixed audio
            elif audio_mix_behavior == "replace":
                if progress_callback and task_id:
                    progress_callback(task_id, {"status": "processing", "message": f"Replacing concatenated video audio with external audio: {os.path.basename(audio_file_path)}"})
                ffmpeg_final_cmd.extend(["-map", "0:v:0", "-map", "1:a:0"]) # Video from concat, audio from external
                ffmpeg_final_cmd.extend(["-c:v", "copy"]) # Try to copy video if compatible
                ffmpeg_final_cmd.extend(["-c:a", "aac", "-b:a", "192k", "-ar", "44100"]) # Re-encode external audio to aac
                ffmpeg_final_cmd.append("-shortest") # Trim to shorter of video/audio
        else:
            # No external audio, just copy the stream from concatenated clips (which now have audio)
            ffmpeg_final_cmd.extend(["-c", "copy"])

        ffmpeg_final_cmd.extend(["-y", output_video_path])

        if not run_ffmpeg_command(ffmpeg_final_cmd, "final video assembly", progress_callback, task_id):
            # Error handled by run_ffmpeg_command
            raise MontageError("Failed to create final video.")

        if progress_callback and task_id:
            final_duration_check = get_video_duration(output_video_path, progress_callback, task_id)
            message = f"Output video created: {os.path.basename(output_video_path)}"
            if final_duration_check:
                message += f" (Duration: {final_duration_check:.2f}s)"
            progress_callback(task_id, {"status": "completed", "message": message, "output_file": output_video_path})

        return output_video_path # Return path on success

    # Temp dir is cleaned up automatically when 'with' block exits
    # No explicit cleanup needed here for temp_dir