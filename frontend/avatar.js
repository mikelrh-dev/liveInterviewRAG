/**
 * InterviewTTS — Three.js energy field orb overlay for Stitch avatar.
 * Three layers: inner orb (custom shader with fresnel + noise), outer halo
 * (soft glow), and energy rings (expanding toruses that react to voice).
 * Blends over the avatar image via screen blend.
 * The image is always visible (graceful degradation if Three.js fails).
 */

(function () {
    'use strict';

    let scene, camera, renderer;
    let innerOrb, orbMaterial, outerHalo, ringGroup;
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

            // ── Layer 1: Inner orb (custom shader — fresnel + animated noise) ──
            orbMaterial = new THREE.ShaderMaterial({
                transparent: true,
                blending: THREE.AdditiveBlending,
                depthWrite: false,
                side: THREE.FrontSide,
                uniforms: {
                    time: { value: 0 },
                    volume: { value: 0 },
                    glowColor: { value: new THREE.Color(0x00d4ff) },
                    intensity: { value: 0.5 },
                },
                vertexShader: `
                    varying vec3 vNormal;
                    varying vec3 vObjPosition;
                    varying vec3 vViewPosition;
                    void main() {
                        vNormal = normalize(normalMatrix * normal);
                        vObjPosition = position;
                        vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
                        vViewPosition = -mvPosition.xyz;
                        gl_Position = projectionMatrix * mvPosition;
                    }
                `,
                fragmentShader: `
                    uniform float time;
                    uniform float volume;
                    uniform vec3  glowColor;
                    uniform float intensity;
                    varying vec3 vNormal;
                    varying vec3 vObjPosition;
                    varying vec3 vViewPosition;

                    float hash(vec3 p) {
                        return fract(sin(dot(p, vec3(12.9898, 78.233, 45.164))) * 43758.5453);
                    }

                    float noise3d(vec3 p) {
                        vec3 i = floor(p);
                        vec3 f = fract(p);
                        f = f * f * (3.0 - 2.0 * f);
                        return mix(
                            mix(mix(hash(i), hash(i + vec3(1.0, 0.0, 0.0)), f.x),
                                mix(hash(i + vec3(0.0, 1.0, 0.0)), hash(i + vec3(1.0, 1.0, 0.0)), f.x), f.y),
                            mix(mix(hash(i + vec3(0.0, 0.0, 1.0)), hash(i + vec3(1.0, 0.0, 1.0)), f.x),
                                mix(hash(i + vec3(0.0, 1.0, 1.0)), hash(i + vec3(1.0, 1.0, 1.0)), f.x), f.y),
                            f.z);
                    }

                    void main() {
                        vec3 viewDir = normalize(vViewPosition);
                        float fresnel = pow(1.0 - abs(dot(vNormal, viewDir)), 2.5);
                        float n = noise3d(vObjPosition * 2.5 + vec3(time * 0.4, time * 0.3, time * 0.5));
                        float pulse = intensity * (0.6 + volume * 1.8);
                        float alpha = fresnel * (0.4 + n * 0.4) * pulse;
                        gl_FragColor = vec4(glowColor * (1.5 + volume * 2.0), clamp(alpha, 0.0, 1.0));
                    }
                `,
            });

            innerOrb = new THREE.Mesh(new THREE.SphereGeometry(1.0, 64, 64), orbMaterial);
            scene.add(innerOrb);

            // ── Layer 2: Outer halo (soft translucent glow) ──
            const haloMat = new THREE.MeshBasicMaterial({
                color: 0x00d4ff,
                transparent: true,
                opacity: 0.12,
                depthWrite: false,
            });
            outerHalo = new THREE.Mesh(new THREE.SphereGeometry(1.5, 48, 48), haloMat);
            scene.add(outerHalo);

            // ── Layer 3: Energy rings (3 toruses that expand on voice) ──
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

        // Idle breathing: ±5% scale (more visible than before)
        idlePhase += 0.025;
        const idleScale = 1.0 + Math.sin(idlePhase) * 0.05;

        // ── Inner orb ──
        const volScale = idleScale + vol * 0.15;
        const orbS = innerOrb.scale.x;
        innerOrb.scale.setScalar(orbS + (volScale - orbS) * 0.2);
        orbMaterial.uniforms.time.value = t;
        orbMaterial.uniforms.volume.value = vol;
        orbMaterial.uniforms.intensity.value = (0.5 + Math.sin(idlePhase * 0.5) * 0.1) * currentBoost;

        // ── Outer halo: subtle pulse ──
        const haloScale = 1.0 + vol * 0.25 + Math.sin(idlePhase * 0.7) * 0.03;
        outerHalo.scale.setScalar(orbS + (haloScale - orbS) * 0.2);
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
        innerOrb.rotation.y += 0.003;
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

        if (orbMaterial) {
            orbMaterial.uniforms.glowColor.value.setHex(c);
        }
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
        setState,
        resize,
        boost,           // NEW
        isInitialized: () => isInitialized,
    };
})();
