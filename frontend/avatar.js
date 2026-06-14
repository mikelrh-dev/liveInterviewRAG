/**
 * InterviewTTS — Three.js energy field overlay for avatar videos.
 * Two layers: outer halo (soft glow) and energy rings (expanding toruses that
 * react to voice). The avatar videos are the primary visual.
 * Graceful degradation if Three.js fails — videos still play.
 */

(function () {
    'use strict';

    let scene, camera, renderer;
    let outerHalo, ringGroup;
    let isInitialized = false;
    let currentVolume = 0;
    let idlePhase = 0;
    let currentBoost = 1.0;

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

            // ── Layer 1: Outer halo (soft translucent glow) ──
            const haloMat = new THREE.MeshBasicMaterial({
                color: 0x00d4ff,
                transparent: true,
                opacity: 0.12,
                depthWrite: false,
            });
            outerHalo = new THREE.Mesh(new THREE.SphereGeometry(1.5, 48, 48), haloMat);
            scene.add(outerHalo);

            // ── Layer 2: Energy rings (3 toruses that expand on voice) ──
            const ringCount = 3;
            ringGroup = new THREE.Group();
            for (let i = 0; i < ringCount; i++) {
                const ringGeom = new THREE.TorusGeometry(1.0, 0.02, 8, 64);
                const ringMat = new THREE.MeshBasicMaterial({
                    color: 0x00d4ff,
                    transparent: true,
                    opacity: 0.0,
                    blending: THREE.AdditiveBlending,
                    depthWrite: false,
                });
                const ring = new THREE.Mesh(ringGeom, ringMat);
                ring.userData = {
                    baseRadius: 1.0,
                    phase: i / ringCount,
                    active: false,
                };
                ring.rotation.x = Math.PI / 2;
                ringGroup.add(ring);
            }
            scene.add(ringGroup);

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

        const t = performance.now() * 0.001;
        const vol = currentVolume;

        // Idle breathing
        idlePhase += 0.025;

        // ── Outer halo: subtle pulse + breathing ──
        const haloScale = 1.0 + vol * 0.25 + Math.sin(idlePhase * 0.7) * 0.03;
        const haloS = outerHalo.scale.x;
        outerHalo.scale.setScalar(haloS + (haloScale - haloS) * 0.2);
        outerHalo.material.opacity = (0.10 + vol * 0.15) * currentBoost;

        // ── Energy rings: emit continuously while there is audio ──
        if (vol > 0.05) {
            ringGroup.children.forEach((ring) => {
                const elapsed = (t + ring.userData.phase * 0.6) % 0.6;
                const ringScale = 1.0 + (elapsed / 0.6) * 0.8;   // 1.0 → 1.8
                ring.scale.setScalar(ringScale);
                ring.material.opacity = Math.max(0, 0.8 * (1.0 - elapsed / 0.6)) * (vol * 2) * currentBoost;
            });
        } else {
            ringGroup.children.forEach((ring) => {
                ring.material.opacity = 0;
            });
        }

        // Gentle continuous rotation
        ringGroup.rotation.z += 0.002;

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
     * Set video blend factor (0-1) driven by audio volume.
     * 0 = fully neutral, 1 = fully talking. Smoothly interpolated via CSS transition.
     * Floor at 0.15 for subtle ambient movement even at low volume.
     */
    function setBlend(vol) {
        const wrapped = Math.max(0, Math.min(1, vol));
        const blend = Math.max(0.15, wrapped);
        const portal = document.getElementById('portal-ring');
        if (portal) {
            portal.style.setProperty('--avatar-blend', blend.toFixed(3));
        }
    }

    /**
     * Set orb state — changes glow color and ring color.
     *   idle:       cyan
     *   listening:  cyan (brighter via volume)
     *   speaking:   violet
     *   processing: amber
     */
    function setState(state) {
        if (!isInitialized) return;

        const colorMap = {
            idle:       0x00d4ff,
            listening:  0x00d4ff,
            speaking:   0x8b5cf6,
            processing: 0xfbbf24,
        };
        const c = colorMap[state] || 0x00d4ff;

        ringGroup.children.forEach((r) => r.material.color.setHex(c));
        if (outerHalo) {
            outerHalo.material.color.setHex(c);
        }
    }

    /**
     * Boost energy field intensity (0.5–2.0 range).
     * Used to amplify orb/halo/rings when AI is speaking.
     */
    function boost(amount) {
        currentBoost = Math.max(0.5, Math.min(2.0, amount));
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
        setBlend,        // NEW — drives video crossfade from TTS volume
        setState,
        resize,
        boost,           // NEW
        isInitialized: () => isInitialized,
    };
})();
