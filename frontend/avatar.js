/**
 * InterviewTTS — Three.js 3D pulsating orb avatar.
 * Renders a glowing cyan sphere that pulses in sync with mic volume.
 * Gracefully falls back to CSS orb if Three.js or WebGL fails.
 */

(function () {
    'use strict';

    let scene, camera, renderer, orb, pointLight;
    let isInitialized = false;
    let currentVolume = 0;
    let targetScale = 1.0;
    let idlePhase = 0;

    const canvas = document.getElementById('orb-canvas');
    const cssFallback = document.getElementById('css-orb-fallback');

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
            camera.position.z = 3;

            renderer = new THREE.WebGLRenderer({
                canvas: canvas,
                alpha: true,
                antialias: true,
            });
            renderer.setSize(160, 160);
            renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

            // Orb geometry
            const geometry = new THREE.SphereGeometry(0.8, 32, 32);
            const material = new THREE.MeshStandardMaterial({
                color: 0x0a0e1a,
                emissive: 0x00d4ff,
                emissiveIntensity: 0.4,
                metalness: 0.3,
                roughness: 0.4,
            });
            orb = new THREE.Mesh(geometry, material);
            scene.add(orb);

            // Point light
            pointLight = new THREE.PointLight(0x00d4ff, 1, 10);
            pointLight.position.set(0, 0, 2);
            scene.add(pointLight);

            // Ambient light
            const ambient = new THREE.AmbientLight(0x404060, 0.5);
            scene.add(ambient);

            isInitialized = true;
            cssFallback.classList.add('hidden');
            canvas.classList.remove('hidden');

            animate();
            return true;
        } catch (e) {
            console.warn('Three.js orb init failed, using CSS fallback:', e.message);
            showFallback();
            return false;
        }
    }

    /**
     * Show CSS fallback orb.
     */
    function showFallback() {
        canvas.classList.add('hidden');
        cssFallback.classList.remove('hidden');
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

        // Emissive intensity driven by volume
        orb.material.emissiveIntensity = 0.4 + volume * 0.8;

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
            orb.material.emissive.setHex(0xfbbf24);
            pointLight.color.setHex(0xfbbf24);
        } else {
            orb.material.emissive.setHex(0x00d4ff);
            pointLight.color.setHex(0x00d4ff);
        }
    }

    /**
     * Resize handler for responsiveness.
     */
    function resize(width, height) {
        if (!isInitialized || !renderer) return;
        renderer.setSize(width, height);
        camera.aspect = width / height;
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
