async function fetchApi<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options)
  if (!response.ok) {
    throw new Error(`API request failed: ${response.statusText}`)
  }
  return response.json()
}

export interface RecordingStatus {
  enabled: boolean
  is_recording: boolean
  frame_count: number
  output_dir: string
}

export interface ScreenshotResult {
  success: boolean
  filepath: string
  timestamp: string
}

export interface RecordingResult {
  success: boolean
  filepath: string
  frame_count?: number
  duration_seconds?: number
  fps?: number
}

export async function fetchRecordingStatus(cameraId: string): Promise<RecordingStatus> {
  return fetchApi<RecordingStatus>(`/api/cameras/${cameraId}/recording/status`)
}

export async function postScreenshot(cameraId: string): Promise<ScreenshotResult> {
  return fetchApi<ScreenshotResult>(`/api/cameras/${cameraId}/screenshot`, {
    method: 'POST',
  })
}

export async function postStartRecording(cameraId: string, fps?: number): Promise<RecordingResult> {
  const query = fps ? `?fps=${fps}` : ''
  return fetchApi<RecordingResult>(`/api/cameras/${cameraId}/recording/start${query}`, {
    method: 'POST',
  })
}

export async function postStopRecording(cameraId: string): Promise<RecordingResult> {
  return fetchApi<RecordingResult>(`/api/cameras/${cameraId}/recording/stop`, {
    method: 'POST',
  })
}
