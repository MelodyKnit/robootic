<template>
  <div class="calibration-panel">
    <div class="panel-header">
      <h3>
        <span class="icon">📷</span>
        相机标定向导
      </h3>
      <button class="close-btn" @click="$emit('close')">✕</button>
    </div>

    <!-- 步骤指示器 -->
    <div class="wizard-steps">
      <div
        v-for="(step, index) in steps"
        :key="index"
        class="step"
        :class="{
          active: currentStep === index,
          completed: currentStep > index
        }"
      >
        <div class="step-number">
          <span v-if="currentStep > index">✓</span>
          <span v-else>{{ index + 1 }}</span>
        </div>
        <div class="step-label">{{ step.label }}</div>
      </div>
    </div>

    <!-- 步骤内容 -->
    <div class="step-content">
      <!-- 步骤1: 准备工作 -->
      <div v-if="currentStep === 0" class="step-prepare">
        <h4>准备工作</h4>

        <div class="status-checks">
          <div class="check-item" :class="{ success: robotReady }">
            <span class="check-icon">{{ robotReady ? '✓' : '○' }}</span>
            <span class="check-label">机器人状态：{{ robotReady ? '已连接 已使能' : '未就绪' }}</span>
          </div>

          <div class="check-item" :class="{ success: cameraReady }">
            <span class="check-icon">{{ cameraReady ? '✓' : '○' }}</span>
            <span class="check-label">相机状态：{{ cameraReady ? `已连接 ${cameraResolution}` : '未就绪' }}</span>
          </div>

          <div class="check-item success">
            <span class="check-icon">✓</span>
            <span class="check-label">标定板：ChArUco 7x5 (25mm)</span>
          </div>
        </div>

        <div class="config-section">
          <h5>工作空间配置</h5>
          <div class="config-row">
            <label>中心点坐标 (mm):</label>
            <div class="input-group">
              <input v-model.number="config.workspace_center_x" type="number" placeholder="X" />
              <input v-model.number="config.workspace_center_y" type="number" placeholder="Y" />
              <input v-model.number="config.workspace_center_z" type="number" placeholder="Z" />
            </div>
          </div>

          <h5>采集参数</h5>
          <div class="config-row">
            <label>目标图像数:</label>
            <input v-model.number="config.target_images" type="number" min="25" max="50" />
            <span class="hint">张（推荐25-35张）</span>
          </div>
        </div>
      </div>

      <!-- 步骤2: 内参标定进行中 -->
      <div v-if="currentStep === 1" class="step-calibrating">
        <h4>内参标定进行中 <span class="spinner" v-if="isRunning">🔄</span></h4>

        <div class="calibration-content">
          <!-- 实时预览区域 -->
          <div class="preview-section">
            <h5>实时相机画面</h5>
            <div class="camera-preview">
              <img :src="cameraPreviewUrl" alt="相机预览" />
              <div v-if="lastDetection" class="detection-overlay">
                <span v-if="lastDetection.detected" class="detected">✓ 检测到标定板</span>
                <span v-else class="not-detected">✗ 未检测到标定板</span>
                <span v-if="lastDetection.cornerCount">角点数: {{ lastDetection.cornerCount }}</span>
              </div>
            </div>
          </div>

          <!-- 3D位姿可视化 -->
          <div class="visualization-section">
            <h5>3D位姿可视化</h5>
            <div class="pose-visualization">
              <canvas ref="poseCanvas" width="300" height="300"></canvas>
              <div class="legend">
                <span><span class="dot captured"></span> 已采集</span>
                <span><span class="dot current"></span> 当前位置</span>
                <span><span class="dot pending"></span> 待采集</span>
              </div>
            </div>
          </div>
        </div>

        <!-- 进度条 -->
        <div class="progress-section">
          <div class="progress-info">
            <span>进度: {{ status.captured_images }}/{{ status.target_images }} 张</span>
            <span>{{ status.progress_percent.toFixed(0) }}%</span>
          </div>
          <div class="progress-bar">
            <div class="progress-fill" :style="{ width: status.progress_percent + '%' }"></div>
          </div>
        </div>

        <!-- 当前状态 -->
        <div class="status-section">
          <div class="status-item">
            <strong>当前位姿:</strong> {{ status.current_pose_description }}
          </div>
          <div class="status-item">
            <strong>状态:</strong> {{ stateText }}
          </div>
        </div>

        <!-- 采集日志 -->
        <div class="log-section">
          <h5>采集日志</h5>
          <div class="log-content" ref="logContent">
            <div
              v-for="(log, index) in logs"
              :key="index"
              class="log-entry"
              :class="log.type"
            >
              <span class="log-time">[{{ log.time }}]</span>
              <span class="log-icon">{{ log.icon }}</span>
              <span class="log-message">{{ log.message }}</span>
            </div>
          </div>
        </div>

        <!-- 控制按钮 -->
        <div class="control-buttons">
          <button
            v-if="status.state === 'running'"
            @click="pauseCalibration"
            class="btn-secondary"
          >
            暂停
          </button>
          <button
            v-if="status.state === 'paused'"
            @click="resumeCalibration"
            class="btn-primary"
          >
            恢复
          </button>
          <button
            @click="stopCalibration"
            class="btn-danger"
          >
            停止
          </button>
        </div>
      </div>

      <!-- 步骤3: 手眼标定（待实施） -->
      <div v-if="currentStep === 2" class="step-handeye">
        <h4>手眼标定</h4>
        <p class="todo-notice">⏳ 手眼标定功能即将推出...</p>
      </div>

      <!-- 步骤4: 验证 -->
      <div v-if="currentStep === 3" class="step-validate">
        <h4>验证标定结果</h4>
        <p class="todo-notice">⏳ 验证功能即将推出...</p>
      </div>

      <!-- 步骤5: 完成 -->
      <div v-if="currentStep === 4" class="step-complete">
        <h4>标定完成 ✓</h4>

        <div class="result-summary">
          <div class="success-icon">🎉</div>
          <h3>标定成功！</h3>

          <div class="result-section">
            <h5>内参标定结果</h5>
            <div class="result-item">
              <span class="label">重投影误差:</span>
              <span class="value success">{{ result.reprojection_error_px?.toFixed(3) }} 像素 ✓</span>
            </div>
            <div class="result-item">
              <span class="label">有效观测数:</span>
              <span class="value">{{ result.views_used }} 张</span>
            </div>
            <div class="result-item">
              <span class="label">焦距 fx:</span>
              <span class="value">{{ result.intrinsics?.fx_px?.toFixed(2) }} px</span>
            </div>
            <div class="result-item">
              <span class="label">焦距 fy:</span>
              <span class="value">{{ result.intrinsics?.fy_px?.toFixed(2) }} px</span>
            </div>
            <div class="result-item">
              <span class="label">主点:</span>
              <span class="value">
                ({{ result.intrinsics?.cx_px?.toFixed(2) }}, {{ result.intrinsics?.cy_px?.toFixed(2) }})
              </span>
            </div>
          </div>

          <div class="result-section">
            <h5>文件已保存</h5>
            <div class="file-item">
              📄 {{ resultFilePath }}
            </div>
          </div>

          <div class="action-buttons">
            <button @click="downloadResult" class="btn-primary">下载标定结果</button>
            <button @click="viewDetailReport" class="btn-secondary">查看详细报告</button>
            <button @click="restartCalibration" class="btn-secondary">重新标定</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 底部导航按钮 -->
    <div class="wizard-footer" v-if="currentStep < 4">
      <button
        @click="prevStep"
        :disabled="currentStep === 0"
        class="btn-secondary"
      >
        &lt; 上一步
      </button>
      <button
        @click="nextStep"
        :disabled="!canProceed"
        class="btn-primary"
      >
        {{ currentStep === 1 ? '开始标定' : '下一步 &gt;' }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue';
import { useCalibration } from '../composables/useCalibration';

const emit = defineEmits(['close']);

// ============================================================================
// 状态管理
// ============================================================================

const steps = [
  { label: '步骤 1/5 - 准备工作' },
  { label: '步骤 2/5 - 内参标定' },
  { label: '步骤 3/5 - 手眼标定' },
  { label: '步骤 4/5 - 验证' },
  { label: '步骤 5/5 - 完成' },
];

const currentStep = ref(0);

// 配置
const config = ref({
  workspace_center_x: 300,
  workspace_center_y: 0,
  workspace_center_z: 100,
  target_images: 30,
});

// 硬件状态
const robotReady = ref(false);
const cameraReady = ref(false);
const cameraResolution = ref('1920x1080');

// 标定状态
const {
  status,
  isRunning,
  logs,
  lastDetection,
  result,
  startCalibration,
  pauseCalibration,
  resumeCalibration,
  stopCalibration,
  connectWebSocket,
  disconnectWebSocket,
} = useCalibration();

// ============================================================================
// 计算属性
// ============================================================================

const canProceed = computed(() => {
  if (currentStep.value === 0) {
    return robotReady.value && cameraReady.value;
  }
  return true;
});

const stateText = computed(() => {
  const stateMap = {
    idle: '空闲',
    ready: '就绪',
    running: '运行中',
    paused: '已暂停',
    stopping: '停止中',
    complete: '已完成',
    error: '错误',
  };
  return stateMap[status.value.state] || status.value.state;
});

const cameraPreviewUrl = computed(() => {
  return '/api/camera/preview/stream';  // 实际URL根据项目调整
});

const resultFilePath = computed(() => {
  return `localstore/calibration/${result.value.calibration_id}_intrinsic.json`;
});

// ============================================================================
// 方法
// ============================================================================

function prevStep() {
  if (currentStep.value > 0) {
    currentStep.value--;
  }
}

async function nextStep() {
  if (currentStep.value === 0) {
    // 准备 → 内参标定
    currentStep.value = 1;
    // 启动标定
    await startIntrinsicCalibration();
  } else if (currentStep.value < steps.length - 1) {
    currentStep.value++;
  }
}

async function startIntrinsicCalibration() {
  const calibrationId = `auto-${Date.now()}`;
  const cameraId = 'hikvision-01';  // 实际ID从状态获取

  await startCalibration({
    calibration_id: calibrationId,
    camera_id: cameraId,
    config: config.value,
  });
}

function downloadResult() {
  // 下载JSON文件
  const blob = new Blob([JSON.stringify(result.value, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${result.value.calibration_id}_intrinsic.json`;
  a.click();
  URL.revokeObjectURL(url);
}

function viewDetailReport() {
  // TODO: 打开详细报告页面
  console.log('查看详细报告');
}

function restartCalibration() {
  currentStep.value = 0;
  // 重置状态
}

// 检查硬件状态
async function checkHardwareStatus() {
  try {
    // TODO: 实际API调用
    robotReady.value = true;
    cameraReady.value = true;
  } catch (error) {
    console.error('检查硬件状态失败:', error);
  }
}

// ============================================================================
// 生命周期
// ============================================================================

onMounted(async () => {
  await checkHardwareStatus();
  connectWebSocket();
});

onUnmounted(() => {
  disconnectWebSocket();
});

// 监听标定完成
watch(() => status.value.state, (newState) => {
  if (newState === 'complete') {
    currentStep.value = 4;  // 跳转到完成步骤
  }
});
</script>

<style scoped>
.calibration-panel {
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
  max-width: 1200px;
  margin: 20px auto;
  overflow: hidden;
}

.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 20px;
  border-bottom: 1px solid #e0e0e0;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
}

.panel-header h3 {
  margin: 0;
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 20px;
}

.close-btn {
  background: rgba(255, 255, 255, 0.2);
  border: none;
  color: white;
  font-size: 24px;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  cursor: pointer;
  transition: background 0.2s;
}

.close-btn:hover {
  background: rgba(255, 255, 255, 0.3);
}

/* 步骤指示器 */
.wizard-steps {
  display: flex;
  justify-content: space-between;
  padding: 30px 40px;
  border-bottom: 1px solid #e0e0e0;
}

.step {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  position: relative;
  opacity: 0.5;
}

.step.active {
  opacity: 1;
}

.step.completed {
  opacity: 0.8;
}

.step-number {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background: #e0e0e0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: bold;
  margin-bottom: 10px;
}

.step.active .step-number {
  background: #667eea;
  color: white;
}

.step.completed .step-number {
  background: #4caf50;
  color: white;
}

.step-label {
  font-size: 12px;
  text-align: center;
}

/* 步骤内容 */
.step-content {
  padding: 30px 40px;
  min-height: 400px;
}

.step-content h4 {
  margin-top: 0;
  font-size: 18px;
  color: #333;
  display: flex;
  align-items: center;
  gap: 10px;
}

/* 准备工作步骤 */
.status-checks {
  margin: 20px 0;
}

.check-item {
  display: flex;
  align-items: center;
  padding: 12px;
  margin: 10px 0;
  border-radius: 6px;
  background: #f5f5f5;
}

.check-item.success {
  background: #e8f5e9;
}

.check-icon {
  font-size: 20px;
  margin-right: 10px;
  width: 24px;
}

.config-section {
  margin-top: 30px;
}

.config-section h5 {
  margin: 20px 0 10px 0;
  color: #666;
  font-size: 14px;
}

.config-row {
  display: flex;
  align-items: center;
  margin: 10px 0;
  gap: 10px;
}

.config-row label {
  min-width: 150px;
  font-weight: 500;
}

.input-group {
  display: flex;
  gap: 10px;
}

.input-group input,
.config-row input[type="number"] {
  padding: 8px 12px;
  border: 1px solid #ddd;
  border-radius: 4px;
  font-size: 14px;
}

.hint {
  color: #999;
  font-size: 12px;
}

/* 标定进行中 */
.calibration-content {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
  margin-bottom: 20px;
}

.preview-section,
.visualization-section {
  border: 1px solid #e0e0e0;
  border-radius: 6px;
  padding: 15px;
}

.preview-section h5,
.visualization-section h5 {
  margin: 0 0 10px 0;
  font-size: 14px;
  color: #666;
}

.camera-preview {
  position: relative;
  background: #000;
  border-radius: 4px;
  overflow: hidden;
}

.camera-preview img {
  width: 100%;
  display: block;
}

.detection-overlay {
  position: absolute;
  bottom: 10px;
  left: 10px;
  right: 10px;
  background: rgba(0, 0, 0, 0.7);
  color: white;
  padding: 8px 12px;
  border-radius: 4px;
  font-size: 12px;
  display: flex;
  gap: 15px;
}

.detected {
  color: #4caf50;
}

.not-detected {
  color: #f44336;
}

.pose-visualization {
  text-align: center;
}

.pose-visualization canvas {
  border: 1px solid #ddd;
  border-radius: 4px;
}

.legend {
  display: flex;
  justify-content: center;
  gap: 20px;
  margin-top: 10px;
  font-size: 12px;
}

.legend .dot {
  display: inline-block;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  margin-right: 5px;
}

.dot.captured {
  background: #4caf50;
}

.dot.current {
  background: #ff9800;
}

.dot.pending {
  background: #e0e0e0;
}

/* 进度条 */
.progress-section {
  margin: 20px 0;
}

.progress-info {
  display: flex;
  justify-content: space-between;
  margin-bottom: 8px;
  font-size: 14px;
  color: #666;
}

.progress-bar {
  height: 24px;
  background: #e0e0e0;
  border-radius: 12px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
  transition: width 0.3s ease;
}

/* 状态和日志 */
.status-section {
  margin: 20px 0;
  padding: 15px;
  background: #f5f5f5;
  border-radius: 6px;
}

.status-item {
  margin: 8px 0;
  font-size: 14px;
}

.log-section h5 {
  margin: 20px 0 10px 0;
  font-size: 14px;
  color: #666;
}

.log-content {
  max-height: 150px;
  overflow-y: auto;
  border: 1px solid #e0e0e0;
  border-radius: 4px;
  padding: 10px;
  background: #fafafa;
  font-family: 'Courier New', monospace;
  font-size: 12px;
}

.log-entry {
  margin: 5px 0;
  display: flex;
  gap: 8px;
}

.log-entry.success {
  color: #4caf50;
}

.log-entry.warning {
  color: #ff9800;
}

.log-entry.error {
  color: #f44336;
}

.log-time {
  color: #999;
}

/* 控制按钮 */
.control-buttons {
  display: flex;
  gap: 10px;
  justify-content: center;
  margin-top: 20px;
}

/* 完成步骤 */
.result-summary {
  text-align: center;
}

.success-icon {
  font-size: 72px;
  margin: 20px 0;
}

.result-section {
  margin: 30px 0;
  padding: 20px;
  border: 1px solid #e0e0e0;
  border-radius: 6px;
  text-align: left;
}

.result-section h5 {
  margin: 0 0 15px 0;
  color: #666;
}

.result-item {
  display: flex;
  justify-content: space-between;
  padding: 10px 0;
  border-bottom: 1px solid #f0f0f0;
}

.result-item:last-child {
  border-bottom: none;
}

.result-item .label {
  font-weight: 500;
}

.result-item .value.success {
  color: #4caf50;
}

.file-item {
  padding: 12px;
  background: #f5f5f5;
  border-radius: 4px;
  font-family: 'Courier New', monospace;
  font-size: 13px;
}

.action-buttons {
  display: flex;
  gap: 10px;
  justify-content: center;
  margin-top: 30px;
}

/* 待办提示 */
.todo-notice {
  text-align: center;
  padding: 60px 20px;
  color: #999;
  font-size: 16px;
}

/* 底部导航 */
.wizard-footer {
  display: flex;
  justify-content: space-between;
  padding: 20px 40px;
  border-top: 1px solid #e0e0e0;
  background: #fafafa;
}

/* 按钮样式 */
.btn-primary {
  padding: 10px 24px;
  background: #667eea;
  color: white;
  border: none;
  border-radius: 6px;
  font-size: 14px;
  cursor: pointer;
  transition: background 0.2s;
}

.btn-primary:hover:not(:disabled) {
  background: #5568d3;
}

.btn-primary:disabled {
  background: #ccc;
  cursor: not-allowed;
}

.btn-secondary {
  padding: 10px 24px;
  background: #e0e0e0;
  color: #333;
  border: none;
  border-radius: 6px;
  font-size: 14px;
  cursor: pointer;
  transition: background 0.2s;
}

.btn-secondary:hover:not(:disabled) {
  background: #d0d0d0;
}

.btn-secondary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-danger {
  padding: 10px 24px;
  background: #f44336;
  color: white;
  border: none;
  border-radius: 6px;
  font-size: 14px;
  cursor: pointer;
  transition: background 0.2s;
}

.btn-danger:hover {
  background: #d32f2f;
}

/* 旋转动画 */
@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.spinner {
  display: inline-block;
  animation: spin 1s linear infinite;
}
</style>
