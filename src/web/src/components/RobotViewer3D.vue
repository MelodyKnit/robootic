<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { RobotScene } from './RobotScene'
import type { JointAngles } from './RobotModel'

const canvasRef = ref<HTMLCanvasElement>()
const loadingModel = ref(false)
const modelError = ref<string | null>(null)
let scene: RobotScene | null = null

// 暴露方法供父组件调用
defineExpose({
  updateJointAngles: (angles: JointAngles) => {
    scene?.updateRobotAngles(angles)
  },
  getRobotAngles: () => {
    return scene?.getRobotAngles() ?? null
  },
  isRobotLoaded: () => {
    return scene?.isRobotLoaded() ?? false
  },
})

onMounted(async () => {
  if (!canvasRef.value) return

  // Initialize scene
  scene = new RobotScene(canvasRef.value)
  scene.startRenderLoop()

  // Load URDF model
  loadingModel.value = true
  modelError.value = null
  try {
    await scene.loadRobotModel('/models/jaka_zu3.urdf')
    console.log('[RobotViewer3D] URDF model loaded successfully')
  } catch (error) {
    console.error('[RobotViewer3D] Failed to load URDF model:', error)
    modelError.value = error instanceof Error ? error.message : 'Unknown error'
  } finally {
    loadingModel.value = false
  }

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
      <!-- 加载提示 -->
      <div v-if="loadingModel" class="loading-indicator">
        <div class="spinner"></div>
        <p class="text-sm text-slate-300">加载机器人模型中...</p>
      </div>

      <!-- 错误提示 -->
      <div v-if="modelError" class="error-indicator">
        <p class="text-sm text-red-400">模型加载失败: {{ modelError }}</p>
      </div>

      <!-- 操作提示 -->
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

.loading-indicator {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.75rem;
  background: rgba(15, 23, 42, 0.9);
  backdrop-filter: blur(4px);
  padding: 1.5rem 2rem;
  border-radius: 0.5rem;
  border: 1px solid rgba(71, 85, 105, 0.3);
}

.spinner {
  width: 2rem;
  height: 2rem;
  border: 3px solid rgba(59, 130, 246, 0.3);
  border-top-color: rgb(59, 130, 246);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.error-indicator {
  position: absolute;
  top: 1rem;
  left: 50%;
  transform: translateX(-50%);
  background: rgba(127, 29, 29, 0.9);
  backdrop-filter: blur(4px);
  padding: 0.75rem 1rem;
  border-radius: 0.375rem;
  border: 1px solid rgba(239, 68, 68, 0.3);
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
