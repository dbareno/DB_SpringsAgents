'use client';

import { useMemo, useRef } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { OrbitControls } from '@react-three/drei';
import * as THREE from 'three';
import type { ThreeJSScene } from '@/services/types';

// ─── Componente interno que renderiza el resorte 3D ──────────────────────────

function HelicalSpring({ params, color }: { params: ThreeJSScene['spring']; color: string }) {
  const meshRef = useRef<THREE.Mesh>(null);

  const geometry = useMemo(() => {
    const {
      wireRadius = 0.5,
      coilRadius = 5,
      totalCoils = 8,
      height = 30,
      tubeSegments = 24,
      radialSegments = 12,
    } = params;

    // Genera los puntos de la curva helicoidal para el tubo
    const points: THREE.Vector3[] = [];
    const totalSegments = Math.max(Math.round(totalCoils * tubeSegments), 48);

    for (let i = 0; i <= totalSegments; i++) {
      const t = i / totalSegments;
      const angle = t * totalCoils * Math.PI * 2;
      const x = coilRadius * Math.cos(angle);
      const z = coilRadius * Math.sin(angle);
      const y = t * height - height / 2;
      points.push(new THREE.Vector3(x, y, z));
    }

    const curve = new THREE.CatmullRomCurve3(points);
    const tubeGeo = new THREE.TubeGeometry(curve, totalSegments, wireRadius, radialSegments, false);
    return tubeGeo;
  }, [params]);

  return (
    <mesh ref={meshRef} geometry={geometry}>
      <meshPhysicalMaterial
        color={color}
        metalness={0.6}
        roughness={0.3}
        clearcoat={0.1}
      />
    </mesh>
  );
}

// ─── Luz ambiental animada sutílmente ─────────────────────────────────────────

function SceneLights() {
  const lightRef = useRef<THREE.DirectionalLight>(null);

  useFrame((state) => {
    if (lightRef.current) {
      const t = state.clock.elapsedTime * 0.15;
      lightRef.current.position.x = Math.sin(t) * 8;
      lightRef.current.position.z = Math.cos(t) * 8;
    }
  });

  return (
    <>
      <ambientLight intensity={0.4} />
      <directionalLight ref={lightRef} position={[5, 10, 5]} intensity={1.2} />
      <directionalLight position={[-5, -5, -5]} intensity={0.3} />
      <hemisphereLight args={['#6688ff', '#442266', 0.3]} />
    </>
  );
}

// ─── Componente contenedor público ────────────────────────────────────────────

interface Spring3DViewerProps {
  scene: ThreeJSScene;
  className?: string;
}

export default function Spring3DViewer({ scene, className }: Spring3DViewerProps) {
  const springColor = scene.material_color || '#4a9eff';
  const background = scene.background || '#0d1117';

  return (
    <div className={className ?? 'w-full aspect-square rounded-xl overflow-hidden border border-zinc-800'}>
      <Canvas
        camera={{ position: [0, 0, 40], fov: 40 }}
        gl={{ antialias: true }}
        style={{ background }}
        className="rounded-xl"
      >
        <SceneLights />
        <HelicalSpring params={scene.spring} color={springColor} />
        <OrbitControls
          enablePan={false}
          minDistance={15}
          maxDistance={80}
          autoRotate
          autoRotateSpeed={1.5}
        />
      </Canvas>
    </div>
  );
}
