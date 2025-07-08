document.addEventListener('DOMContentLoaded', () => {
    // GSAP animations
    gsap.fromTo(".container", { opacity: 0, y: 50 }, { opacity: 1, y: 0, duration: 0.8 });
    gsap.from("#main-title", { opacity: 0, y: -30, duration: 0.6, delay: 0.3 });
    gsap.from('.form-section', { opacity: 0, y: 30, duration: 0.5, stagger: 0.2, delay: 0.5 });

    const form = document.getElementById('montage-form');
    const submitButton = document.getElementById('submit-button');
    const progressSection = document.getElementById('progress-section');
    const progressBar = document.getElementById('progress-bar');
    const progressText = document.getElementById('progress-text');
    const videoPlayerContainer = document.getElementById('video-player-container');
    const outputVideoPlayer = document.getElementById('output-video-player');
    const downloadLinkContainer = document.getElementById('download-link-container');
    const downloadLink = document.getElementById('download-link');

    const inputVideoUrl = document.getElementById('input_video_url');
    const inputVideoFile = document.getElementById('input_video_file');
    const serverVideoSelect = document.getElementById('server_video_filename');
    const refreshServerFilesBtn = document.getElementById('refresh-server-files');

    let taskId = null;
    let pollInterval = null;

    // --- Server File Dropdown Logic ---
    async function fetchAndPopulateServerVideos() {
        serverVideoSelect.innerHTML = '<option value="" selected>Loading...</option>';
        try {
            const response = await fetch('/list_server_videos');
            if (!response.ok) throw new Error('Failed to fetch file list.');
            const files = await response.json();
            
            serverVideoSelect.innerHTML = ''; // Clear
            const placeholder = document.createElement('option');
            placeholder.value = '';
            placeholder.textContent = '-- Select a server video --';
            serverVideoSelect.appendChild(placeholder);

            if (files.length > 0) {
                files.forEach(file => {
                    const option = document.createElement('option');
                    option.value = file;
                    option.textContent = file;
                    serverVideoSelect.appendChild(option);
                });
            } else {
                placeholder.textContent = 'No videos found on server';
            }
        } catch (error) {
            serverVideoSelect.innerHTML = '<option value="" disabled selected>Error loading files</option>';
        }
    }
    fetchAndPopulateServerVideos();
    refreshServerFilesBtn.addEventListener('click', fetchAndPopulateServerVideos);

    // --- Logic to clear other inputs when one is used ---
    function clearOtherInputs(activeInput) {
        if (activeInput !== 'url') inputVideoUrl.value = '';
        if (activeInput !== 'file') inputVideoFile.value = '';
        if (activeInput !== 'server') serverVideoSelect.value = '';
    }
    inputVideoUrl.addEventListener('input', () => clearOtherInputs('url'));
    inputVideoFile.addEventListener('change', () => clearOtherInputs('file'));
    serverVideoSelect.addEventListener('change', () => clearOtherInputs('server'));

    form.addEventListener('submit', async (event) => {
        event.preventDefault();

        submitButton.disabled = true;
        submitButton.textContent = 'Processing...';
        progressSection.style.display = 'block';
        progressBar.style.width = '0%';
        progressBar.style.backgroundColor = '#3498db';
        progressText.textContent = 'Initializing and uploading...';
        videoPlayerContainer.style.display = 'none';
        downloadLinkContainer.style.display = 'none';

        const formData = new FormData(form);

        try {
            const response = await fetch('/create_montage', {
                method: 'POST',
                body: formData,
            });

            // --- FIXED: REVERTED TO ROBUST ERROR HANDLING ---
            if (!response.ok) {
                let errorMessage = `Server error: ${response.status}`;
                try {
                    const errorData = await response.json();
                    errorMessage = errorData.error || JSON.stringify(errorData);
                } catch (e) {
                    errorMessage = `Server error ${response.status}: Failed to parse error response.`;
                }
                throw new Error(errorMessage);
            }

            const data = await response.json();
            if (data.task_id) {
                taskId = data.task_id;
                progressText.textContent = data.message || 'Processing started.';
                pollProgress();
            } else {
                throw new Error("Server did not return a task ID.");
            }

        } catch (error) {
            console.error('Error submitting form:', error);
            progressText.textContent = `Submit Error: ${error.message}`;
            progressBar.style.backgroundColor = '#e74c3c';
            progressBar.style.width = '100%';
            submitButton.disabled = false;
            submitButton.textContent = 'Create Montage';
        }
    });

    function pollProgress() {
        if (pollInterval) clearInterval(pollInterval);
        pollInterval = setInterval(async () => {
            if (!taskId) return clearInterval(pollInterval);
            try {
                const response = await fetch(`/status/${taskId}`);
                if (!response.ok) return; // Don't stop polling for a single network hiccup
                const data = await response.json();
                if (!data || data.task_id !== taskId) return;

                progressText.textContent = data.message || '...';
                
                if (data.status === 'processing') {
                    let currentWidth = parseFloat(progressBar.style.width) || 0;
                    progressBar.style.width = (currentWidth < 95 ? currentWidth + 2 : 95) + '%';
                } else if (data.status === 'completed') {
                    clearInterval(pollInterval);
                    progressBar.style.width = '100%';
                    progressBar.style.backgroundColor = '#2ecc71';
                    if (data.output_file && data.task_id) {
                        const videoFileName = data.output_file;
                        outputVideoPlayer.src = `/stream/${data.task_id}/${videoFileName}`;
                        videoPlayerContainer.style.display = 'block';
                        downloadLink.href = `/download/${data.task_id}/${videoFileName}`;
                        downloadLinkContainer.style.display = 'block';
                    }
                    submitButton.disabled = false;
                    submitButton.textContent = 'Create Another';
                    taskId = null;
                } else if (data.status === 'error') {
                    clearInterval(pollInterval);
                    progressBar.style.width = '100%';
                    progressBar.style.backgroundColor = '#e74c3c';
                    submitButton.disabled = false;
                    submitButton.textContent = 'Try Again';
                    taskId = null;
                }
            } catch (error) {
                console.error('Polling error:', error);
            }
        }, 2000);
    }
});