import { ref, onMounted, onBeforeUnmount } from 'vue'
import {
  fetchRecordingStatus,
  postScreenshot,
  postStartRecording,
  postStopRecording,
  type RecordingStatus,
} from '../api/recording'

export function useRecording(cameraIdGetter: () => string) {
  const status = ref<RecordingStatus | null>(null)
  const isRecording = ref(false)
  const frameCount = ref(0)
  const isOperating = ref(false)
  const message = ref('')
  const isError = ref(false)

  let pollTimer: ReturnType<typeof setInterval> | null = null

  async function updateStatus() {
    try {
      const cameraId = cameraIdGetter()
      if (!cameraId) return
      const res = await fetchRecordingStatus(cameraId)
      status.value = res
      isRecording.value = res.is_recording
      frameCount.value = res.frame_count
    } catch (e) {
      // transient network error or recording disabled
    }
  }

  async function takeScreenshot() {
    const cameraId = cameraIdGetter()
    if (!cameraId || isOperating.value) return
    isOperating.value = true
    message.value = ''
    isError.value = false

    try {
      const res = await postScreenshot(cameraId)
      message.value = `截图已保存: ${res.filepath}`
      isError.value = false
    } catch (e: any) {
      message.value = `截图失败: ${e.message || e}`
      isError.value = true
    } finally {
      isOperating.value = false
    }
  }

  async function startRecording(fps?: number) {
    const cameraId = cameraIdGetter()
    if (!cameraId || isOperating.value) return
    isOperating.value = true
    message.value = ''
    isError.value = false

    try {
      const res = await postStartRecording(cameraId, fps)
      isRecording.value = true
      message.value = `录制已开始 -> ${res.filepath}`
      isError.value = false
      await updateStatus()
    } catch (e: any) {
      message.value = `开始录制失败: ${e.message || e}`
      isError.value = true
    } finally {
      isOperating.value = false
    }
  }

  async function stopRecording() {
    const cameraId = cameraIdGetter()
    if (!cameraId || isOperating.value) return
    isOperating.value = true
    message.value = ''
    isError.value = false

    try {
      const res = await postStopRecording(cameraId)
      isRecording.value = false
      message.value = `录制已结束: ${res.filepath} (${res.frame_count}帧, ${res.duration_seconds}秒)`
      isError.value = false
      await updateStatus()
    } catch (e: any) {
      message.value = `停止录制失败: ${e.message || e}`
      isError.value = true
    } finally {
      isOperating.value = false
    }
  }

  onMounted(() => {
    updateStatus()
    pollTimer = setInterval(updateStatus, 1000)
  })

  onBeforeUnmount(() => {
    if (pollTimer) clearInterval(pollTimer)
  })

  return {
    status,
    isRecording,
    frameCount,
    isOperating,
    message,
    isError,
    takeScreenshot,
    startRecording,
    stopRecording,
    updateStatus,
  }
}
