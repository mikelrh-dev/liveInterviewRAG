/**
 * InterviewTTS — Three.js energy field overlay for avatar videos.
 * Two layers: outer halo (soft glow) and energy rings (expanding toruses that
 * react to voice). The avatar videos are the primary visual.
 * Graceful degradation if Three.js fails — videos still play.
 *
 * Honours `prefers-reduced-motion`, which the stylesheet already did for CSS
 * animation. Reduced motion here means the orb stops *animating*, not that it
 * stops working: state changes and resizes still repaint, because that is how
 * the avatar says whether it is listening, speaking or processing. What stops
 * is the free-running part — the idle breathing and the perpetual rotation.
 * Per-frame mic volume does not repaint under reduced motion, since a
 * continuous decorative response is exactly what the user asked us not to do.
 */

(function () {
    'use strict';

    const REDUCED_MOTION_QUERY = '(prefers-reduced-motion: reduce)';

    let scene, camera, renderer;
    let outerHalo, ringGroup;
    let isInitialized = false;
    let currentVolume = 0;
    let idlePhase = 0;
    let currentBoost = 1.0;
    let frameHandle = null;
    let reducedMotion = false;

    const canvas = document.getElementById('orb-canvas');

    // Read live, not at load: the user can change this mid-session, and a
    // snapshotted preference would ignore them.
    const motionQuery = typeof window.matchMedia === 'function'
        ? window.matchMedia(REDUCED_MOTION_QUERY)
        : null;

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

            if (motionQuery) {
                reducedMotion = motionQuery.matches;
                if (typeof motionQuery.addEventListener === 'function') {
                    motionQuery.addEventListener('change', onMotionPreferenceChange);
                } else if (typeof motionQuery.addListener === 'function') {
                    motionQuery.addListener(onMotionPreferenceChange); // Safari < 14
                }
            }

            startLoop();
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
     * Animation loop (60fps target). Under reduced motion this draws one
     * static frame and arms nothing: a user who asked the OS to reduce motion
     * gets a still orb, not a slower one.
     */
    function animate() {
        if (!isInitialized) return;

        if (reducedMotion) {
            renderFrame();
            return;
        }

        frameHandle = requestAnimationFrame(animate);
        renderFrame();
    }

    /**
     * Begin the loop, unless it is already running. Idempotent so a
     * preference change that arrives twice cannot double the loop.
     */
    function startLoop() {
        if (!isInitialized || frameHandle !== null) return;
        animate();
    }

    /**
     * Cancel the pending frame and leave one correct frame behind, so the orb
     * freezes in the state it was in rather than on a stale one.
     */
    function stopLoop() {
        if (frameHandle !== null) {
            cancelAnimationFrame(frameHandle);
            frameHandle = null;
        }
        if (isInitialized) {
            renderFrame();
        }
    }

    /**
     * Follow the OS preference whenever it changes mid-session.
     */
    function onMotionPreferenceChange(event) {
        reducedMotion = event.matches;
        if (reducedMotion) {
            stopLoop();
        } else {
            startLoop();
        }
    }

    /**
     * Draw one frame. Split out of animate() so the discrete events that carry
     * meaning — a state change, a resize, a preference flip — can repaint
     * without a loop running behind them.
     */
    function renderFrame() {
        if (!isInitialized) return;

        const t = performance.now() * 0.001;
        const vol = currentVolume;
        // The still frame: no breathing, no creep.
        const live = !reducedMotion;

        // Idle breathing
        if (live) {
            idlePhase += 0.025;
        }

        // ── Outer halo: subtle pulse + breathing ──
        const breath = live ? Math.sin(idlePhase * 0.7) * 0.03 : 0;
        const haloScale = 1.0 + vol * 0.25 + breath;
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
        if (live) {
            ringGroup.rotation.z += 0.002;
        }

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

        // With the loop stopped there is no next frame to pick this up, and
        // the state colour IS the avatar's job — paint it now.
        if (reducedMotion) {
            renderFrame();
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

        // setSize clears the drawing buffer, so a stopped loop has to repaint
        // or the resized orb goes blank.
        if (reducedMotion) {
            renderFrame();
        }
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
