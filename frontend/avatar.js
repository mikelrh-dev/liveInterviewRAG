/**
 * InterviewTTS — Three.js translucent additive orb over Stitch avatar image.
 * Renders a glowing cyan sphere that pulses with mic volume and overlays
 * the avatar image via screen blend. The image is always visible (graceful
 * degradation if Three.js fails).
 */

(function () {
    'use strict';

    let scene, camera, renderer, orb;
    let isInitialized = false;
    let currentVolume = 0;
    let targetScale = 1.0;
    let idlePhase = 0;

    const canvas = document.getElementById('orb-canvas');

    /**
     * Initialize the Three.js scene. Returns true on success, false on failure.
     */
    function init() {
        try {
            // Check Three.js loaded
            if (typeof THREE === 'undefined') {
                throw new Error('Three.js not loaded');
            }

            // Check WebGL support
            const testCanvas = document.createElement('canvas');
            const gl = testCanvas.getContext('webgl') || testCanvas.getContext('experimental-webgl');
            if (!gl) {
                throw new Error('WebGL not supported');
            }

            scene = new THREE.Scene();

            camera = new THREE.PerspectiveCamera(50, 1, 0.1, 100);
            camera.position.z = 5;

            renderer = new THREE.WebGLRenderer({
                canvas: canvas,
                alpha: true,
                antialias: true,
            });
            renderer.setSize(500, 500);
            renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

            // Translucent additive sphere — overlays the image as a glow
            const geometry = new THREE.SphereGeometry(1.2, 48, 48);
            const material = new THREE.MeshBasicMaterial({
                color: 0x00d4ff,
                transparent: true,
                opacity: 0.4,
                blending: THREE.AdditiveBlending,
                depthWrite: false,
            });
            orb = new THREE.Mesh(geometry, material);
            scene.add(orb);

            isInitialized = true;
            canvas.classList.remove('hidden');

            animate();
            return true;
        } catch (e) {
            console.warn('Three.js orb init failed, using image fallback:', e.message);
            showFallback();
            return false;
        }
    }

    /**
     * Graceful fallback — hides the canvas, the image is always visible.
     */
    function showFallback() {
        canvas.classList.add('hidden');
    }

    /**
     * Animation loop (60fps target).
     */
    function animate() {
        if (!isInitialized) return;

        requestAnimationFrame(animate);

        // Smooth volume interpolation
        const volume = currentVolume;

        // Idle breathing
        idlePhase += 0.02;
        const idleScale = 1.0 + Math.sin(idlePhase) * 0.025;

        // Volume-driven scale (1.0–1.3 range)
        targetScale = idleScale + volume * 0.3;

        // Apply scale with smoothing
        const s = orb.scale.x;
        const newScale = s + (targetScale - s) * 0.15;
        orb.scale.setScalar(newScale);

        // Opacity driven by volume (0.3–0.6 range)
        orb.material.opacity = 0.3 + volume * 0.3;

        // Gentle rotation
        orb.rotation.y += 0.005;

        renderer.render(scene, camera);
    }

    /**
     * Update orb from mic volume (0–1 range).
     * Called from app.js on each animation frame.
     */
    function setVolume(vol) {
        currentVolume = Math.max(0, Math.min(1, vol));
    }

    /**
     * Set orb state for processing (amber glow).
     */
    function setState(state) {
        if (!isInitialized || !orb) return;

        if (state === 'processing') {
            orb.material.color.setHex(0xfbbf24);
        } else {
            orb.material.color.setHex(0x00d4ff);
        }
    }

    /**
     * Resize handler for responsiveness.
     */
    function resize(width, height) {
        if (!isInitialized || !renderer) return;
        const w = width || 500;
        const h = height || 500;
        renderer.setSize(w, h);
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
    }

    // Public API
    window.AvatarOrb = {
        init,
        setVolume,
        setState,
        resize,
        isInitialized: () => isInitialized,
    };
})();
