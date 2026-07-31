<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { RobotScene } from './RobotScene'

const canvasRef = ref<HTMLCanvasElement>()
let scene: RobotScene | null = null

onMounted(() => {
  if (!canvasRef.value) return

  // Initialize scene
  scene = new RobotScene(canvasRef.value)
  scene.startRenderLoop()

  // Handle window resize
  const resizeObserver = new ResizeObserver((entries) => {
    const entry = entries[0]
    if (entry && scene) {
      const { width, height } = entry.contentRect
      scene.handleResize(width, height)
    }
  })

  const container = canvasRef.value.parentElement
  if (container) {
    resizeObserver.observe(container)
  }

  // Cleanup resize observer
  onUnmounted(() => {
    resizeObserver.disconnect()
  })
})

onUnmounted(() => {
  scene?.dispose()
  scene = null
})
</script>

<template>
  <div class="robot-viewer-3d">
    <canvas ref="canvasRef"></canvas>
    <div class="viewer-overlay">
      <div class="viewer-info">
        <p class="text-xs text-slate-400">
          鼠标左键: 旋转 | 右键: 平移 | 滚轮: 缩放
        </p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.robot-viewer-3d {
  width: 100%;
  height: 100%;
  position: relative;
  background: #0f172a;
  border-radius: 0.5rem;
  overflow: hidden;
}

canvas {
  width: 100%;
  height: 100%;
  display: block;
}

.viewer-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  pointer-events: none;
}

.viewer-info {
  position: absolute;
  bottom: 1rem;
  left: 1rem;
  background: rgba(15, 23, 42, 0.8);
  backdrop-filter: blur(4px);
  padding: 0.5rem 0.75rem;
  border-radius: 0.375rem;
  border: 1px solid rgba(71, 85, 105, 0.3);
}
</style>
