/**
 * 标定功能 Composable
 *
 * 提供标定状态管理、API调用和WebSocket连接
 */

import { ref, reactive, computed } from 'vue';

interface CalibrationStatus {
  phase: string;
  state: string;
  current_pose: number;
  total_poses: number;
  captured_images: number;
  target_images: number;
  current_pose_description: string;
  progress_percent: number;
  error_message?: string;
}

interface CalibrationLog {
  time: string;
  type: 'success' | 'warning' | 'error' | 'info';
  icon: string;
  message: string;
}

interface LastDetection {
  detected: boolean;
  cornerCount?: number;
}

export function useCalibration() {
  // ============================================================================
  // 状态
  // ============================================================================

  const status = ref<CalibrationStatus>({
    phase: 'idle',
    state: 'idle',
    current_pose: 0,
    total_poses: 0,
    captured_images: 0,
    target_images: 30,
    current_pose_description: '',
    progress_percent: 0,
  });

  const logs = ref<CalibrationLog[]>([]);
  const lastDetection = ref<LastDetection | null>(null);
  const result = ref<any>({});

  let ws: WebSocket | null = null;

  // ============================================================================
  // 计算属性
  // ============================================================================

  const isRunning = computed(() => status.value.state === 'running');
  const isPaused = computed(() => status.value.state === 'paused');
  const isComplete = computed(() => status.value.state === 'complete');

  // ============================================================================
  // API调用
  // ============================================================================

  async function startCalibration(params: {
    calibration_id: string;
    camera_id: string;
    config?: any;
  }) {
    try {
      // 如果提供了配置，先更新配置
      if (params.config) {
        await updateConfig(params.config);
      }

      // 启动标定
      const response = await fetch('/api/calibration/intrinsic/start', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          calibration_id: params.calibration_id,
          camera_id: params.camera_id,
        }),
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || '启动标定失败');
      }

      const data = await response.json();
      addLog('info', '标定已启动');
      return data;
    } catch (error: any) {
      addLog('error', `启动失败: ${error.message}`);
      throw error;
    }
  }

  async function pauseCalibration() {
    try {
      const response = await fetch('/api/calibration/intrinsic/pause', {
        method: 'POST',
      });

      if (!response.ok) {
        throw new Error('暂停标定失败');
      }

      addLog('warning', '标定已暂停');
    } catch (error: any) {
      addLog('error', `暂停失败: ${error.message}`);
      throw error;
    }
  }

  async function resumeCalibration() {
    try {
      const response = await fetch('/api/calibration/intrinsic/resume', {
        method: 'POST',
      });

      if (!response.ok) {
        throw new Error('恢复标定失败');
      }

      addLog('info', '标定已恢复');
    } catch (error: any) {
      addLog('error', `恢复失败: ${error.message}`);
      throw error;
    }
  }

  async function stopCalibration() {
    try {
      const response = await fetch('/api/calibration/intrinsic/stop', {
        method: 'POST',
      });

      if (!response.ok) {
        throw new Error('停止标定失败');
      }

      addLog('warning', '标定已停止');
    } catch (error: any) {
      addLog('error', `停止失败: ${error.message}`);
      throw error;
    }
  }

  async function fetchStatus() {
    try {
      const response = await fetch('/api/calibration/intrinsic/status');

      if (!response.ok) {
        throw new Error('获取状态失败');
      }

      const data = await response.json();
      status.value = data;
      return data;
    } catch (error: any) {
      console.error('获取状态失败:', error);
      throw error;
    }
  }

  async function updateConfig(config: any) {
    try {
      const response = await fetch('/api/calibration/config', {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(config),
      });

      if (!response.ok) {
        throw new Error('更新配置失败');
      }

      addLog('info', '配置已更新');
    } catch (error: any) {
      addLog('error', `更新配置失败: ${error.message}`);
      throw error;
    }
  }

  async function fetchHistory() {
    try {
      const response = await fetch('/api/calibration/results');

      if (!response.ok) {
        throw new Error('获取历史记录失败');
      }

      const data = await response.json();
      return data.data || [];
    } catch (error: any) {
      console.error('获取历史记录失败:', error);
      return [];
    }
  }

  async function fetchResult(calibrationId: string) {
    try {
      const response = await fetch(`/api/calibration/results/${calibrationId}`);

      if (!response.ok) {
        throw new Error('获取结果失败');
      }

      const data = await response.json();
      return data.data;
    } catch (error: any) {
      console.error('获取结果失败:', error);
      return null;
    }
  }

  async function deleteResult(calibrationId: string) {
    try {
      const response = await fetch(`/api/calibration/results/${calibrationId}`, {
        method: 'DELETE',
      });

      if (!response.ok) {
        throw new Error('删除结果失败');
      }

      addLog('info', `已删除标定结果: ${calibrationId}`);
    } catch (error: any) {
      addLog('error', `删除失败: ${error.message}`);
      throw error;
    }
  }

  // ============================================================================
  // WebSocket连接
  // ============================================================================

  function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/api/calibration/ws`;

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      console.log('WebSocket已连接');
      addLog('info', 'WebSocket已连接');
    };

    ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        handleWebSocketMessage(message);
      } catch (error) {
        console.error('解析WebSocket消息失败:', error);
      }
    };

    ws.onerror = (error) => {
      console.error('WebSocket错误:', error);
      addLog('error', 'WebSocket连接错误');
    };

    ws.onclose = () => {
      console.log('WebSocket已断开');
      addLog('warning', 'WebSocket已断开');

      // 尝试重连（可选）
      setTimeout(() => {
        if (ws && ws.readyState === WebSocket.CLOSED) {
          console.log('尝试重连WebSocket...');
          connectWebSocket();
        }
      }, 3000);
    };
  }

  function disconnectWebSocket() {
    if (ws) {
      ws.close();
      ws = null;
    }
  }

  function handleWebSocketMessage(message: any) {
    const { type, data } = message;

    switch (type) {
      case 'calibration.progress':
        // 更新进度
        status.value = {
          ...status.value,
          ...data,
        };
        break;

      case 'calibration.pose_complete':
        // 位姿采集完成
        if (data.success) {
          addLog(
            'success',
            `[${data.pose_index + 1}/${status.value.total_poses}] ${data.description} - 角点:${data.corner_count}`
          );
          lastDetection.value = {
            detected: true,
            cornerCount: data.corner_count,
          };
        } else {
          addLog(
            'warning',
            `[跳过] ${data.description} - ${data.error_message || '未检测到标定板'}`
          );
          lastDetection.value = {
            detected: false,
          };
        }
        break;

      case 'calibration.phase_complete':
        // 阶段完成
        addLog('success', `${data.phase}标定完成`);
        result.value = data.result;
        status.value.state = 'complete';
        break;

      case 'calibration.error':
        // 错误
        addLog('error', data.message);
        status.value.state = 'error';
        status.value.error_message = data.message;
        break;

      case 'calibration.state_changed':
        // 状态变更
        status.value.state = data.state;
        break;

      case 'pong':
        // 心跳响应
        break;

      default:
        console.log('未知消息类型:', type, data);
    }
  }

  // ============================================================================
  // 日志管理
  // ============================================================================

  function addLog(type: CalibrationLog['type'], message: string) {
    const now = new Date();
    const time = `${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')}:${now.getSeconds().toString().padStart(2, '0')}`;

    const iconMap = {
      success: '✓',
      warning: '⚠',
      error: '✗',
      info: 'ℹ',
    };

    logs.value.push({
      time,
      type,
      icon: iconMap[type],
      message,
    });

    // 限制日志数量
    if (logs.value.length > 100) {
      logs.value.shift();
    }
  }

  function clearLogs() {
    logs.value = [];
  }

  // ============================================================================
  // 返回
  // ============================================================================

  return {
    // 状态
    status,
    logs,
    lastDetection,
    result,

    // 计算属性
    isRunning,
    isPaused,
    isComplete,

    // 方法
    startCalibration,
    pauseCalibration,
    resumeCalibration,
    stopCalibration,
    fetchStatus,
    updateConfig,
    fetchHistory,
    fetchResult,
    deleteResult,

    // WebSocket
    connectWebSocket,
    disconnectWebSocket,

    // 日志
    addLog,
    clearLogs,
  };
}
