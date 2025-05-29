document.addEventListener('DOMContentLoaded', () => {
    // ... (GSAP animations remain the same) ...
    gsap.fromTo(".container", { opacity: 0, y: 50 }, { opacity: 1, y: 0, duration: 0.8, ease: "power2.out" });
    gsap.from("#main-title", { opacity: 0, y: -30, duration: 0.6, delay: 0.3, ease: "power1.out" });
    
    const formSections = document.querySelectorAll('.form-section');
    gsap.from(formSections, {
        opacity: 0,
        y: 30,
        duration: 0.5,
        stagger: 0.2,
        delay: 0.5,
        ease: "power1.out"
    });


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


    let taskId = null;
    let pollInterval = null;

    // Optional: Logic to clear one input if the other is used
    inputVideoUrl.addEventListener('input', () => {
        if (inputVideoUrl.value.trim() !== '') {
            inputVideoFile.value = ''; // Clear file input
        }
    });
    inputVideoFile.addEventListener('change', () => {
        if (inputVideoFile.files.length > 0) {
            inputVideoUrl.value = ''; // Clear URL input
        }
    });


    form.addEventListener('submit', async (event) => {
        event.preventDefault();

        if (!inputVideoUrl.value.trim() && inputVideoFile.files.length === 0) {
            alert("Please provide a video URL or upload a video file.");
            return;
        }
         if (inputVideoUrl.value.trim() && inputVideoFile.files.length > 0) {
            alert("Please use EITHER a video URL OR upload a file, not both.");
            return;
        }


        submitButton.disabled = true;
        submitButton.textContent = 'Processing...';
        
        progressSection.style.display = 'block';
        gsap.fromTo(progressSection, {opacity:0, height: 0}, {opacity:1, height: 'auto', duration: 0.5, ease: 'power2.out'});
        
        progressBar.style.width = '0%';
        progressBar.style.backgroundColor = '#2ecc71'; 
        progressBar.textContent = '';
        progressText.textContent = 'Initializing and uploading...';
        
        videoPlayerContainer.style.display = 'none'; // Hide player initially
        outputVideoPlayer.src = ''; // Clear previous video
        downloadLinkContainer.style.display = 'none';

        const formData = new FormData(form);

        try {
            const response = await fetch('/create_montage', {
                method: 'POST',
                body: formData,
            });

            if (!response.ok) {
                let errorMessage = `Server error: ${response.status}`;
                try {
                    const errorData = await response.json();
                    errorMessage = errorData.error || JSON.stringify(errorData) || errorMessage;
                } catch (e) {
                    const errorText = await response.text();
                    errorMessage = `Server error ${response.status}: ${errorText.substring(0, 300)}...`;
                }
                throw new Error(errorMessage);
            }

            const data = await response.json();
            
            if (data.task_id) {
                taskId = data.task_id;
                progressText.textContent = data.message || 'Processing started. Please wait...';
                pollProgress();
            } else {
                throw new Error("Server response successful, but task ID missing.");
            }

        } catch (error) {
            console.error('Error submitting form:', error);
            progressText.textContent = `Submit Error: ${error.message}`;
            progressBar.style.backgroundColor = '#e74c3c'; 
            progressBar.style.width = '100%';
            progressBar.textContent = 'Failed';
            submitButton.disabled = false;
            submitButton.textContent = 'Create Montage';
        }
    });

    function pollProgress() {
        if (pollInterval) clearInterval(pollInterval);

        pollInterval = setInterval(async () => {
            if (!taskId) {
                clearInterval(pollInterval);
                return;
            }

            try {
                // Use task_id from the closure, or from data if server includes it in status
                const currentTaskId = taskId; 
                const response = await fetch(`/status/${currentTaskId}`); 
                if (!response.ok) {
                    let pollErrorMessage = `Polling failed: ${response.status}`;
                     try {
                        const errorData = await response.json();
                        pollErrorMessage = errorData.message || errorData.error || pollErrorMessage;
                    } catch (e) {
                        const errorText = await response.text(); // Get raw text if not JSON
                        pollErrorMessage = `Polling error ${response.status}: ${errorText.substring(0,100)}...`;
                    }
                    progressText.textContent = pollErrorMessage;
                    console.warn("Polling issue for task", currentTaskId, ":", pollErrorMessage);
                    // Do not stop polling on a single failed status check, maybe server is temp.
                    return; 
                }
                const data = await response.json();

                // Important: Ensure we are updating UI for the correct task,
                // especially if user starts a new task while an old one is polling.
                if (data.task_id !== currentTaskId && taskId !== null) { // taskId might be null if completed/errored
                    console.warn(`Polling received status for task ${data.task_id}, but current task is ${currentTaskId}. Ignoring.`);
                    return;
                }


                progressText.textContent = data.message || 'Processing...';

                if (data.status === 'processing') {
                    let currentWidth = parseFloat(progressBar.style.width) || 0;
                    if (currentWidth < 95) { 
                       progressBar.style.width = (currentWidth + 2 > 95 ? 95 : currentWidth + 2) + '%';
                    }
                     progressBar.style.backgroundColor = '#2ecc71';
                } else if (data.status === 'completed') {
                    clearInterval(pollInterval);
                    progressBar.style.width = '100%';
                    progressBar.style.backgroundColor = '#2ecc71'; 
                    progressBar.textContent = 'Completed!';
                    if (data.output_file && data.task_id) {
                        const videoFileName = data.output_file.split('/').pop();
                        // Use the new /stream endpoint for the video player
                        outputVideoPlayer.src = `/stream/${data.task_id}/${videoFileName}`;
                        videoPlayerContainer.style.display = 'block';
                        gsap.fromTo(videoPlayerContainer, {opacity:0, y:20}, {opacity:1, y:0, duration:0.5, ease:'power2.out'});


                        downloadLink.href = `/download/${data.task_id}/${videoFileName}`;
                        downloadLinkContainer.style.display = 'block';
                        gsap.fromTo(downloadLinkContainer, {opacity:0, scale:0.8}, {opacity:1, scale:1, duration:0.5, ease:'back.out(1.7)'});
                    }
                    submitButton.disabled = false;
                    submitButton.textContent = 'Create Another Montage';
                    taskId = null; 
                } else if (data.status === 'error') {
                    clearInterval(pollInterval);
                    progressBar.style.width = '100%';
                    progressBar.style.backgroundColor = '#e74c3c'; 
                    progressBar.textContent = 'Error!';
                    videoPlayerContainer.style.display = 'none'; // Hide player on error
                    submitButton.disabled = false;
                    submitButton.textContent = 'Try Again';
                    taskId = null; 
                } else if (data.status === 'queued') {
                     progressBar.style.backgroundColor = '#3498db';
                }
            } catch (error) {
                console.error('Error polling progress:', error);
                progressText.textContent = `Polling system error: ${error.message}. Retrying...`;
            }
        }, 2000);
    }
});