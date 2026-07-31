<!--
  机械臂列表使用示例
  展示如何在现有组件中集成机械臂列表功能
-->

<template>
  <div class="p-6 space-y-6">
    <!-- 页面标题 -->
    <div>
      <h1 class="text-2xl font-bold text-slate-200">机械臂控制中心</h1>
      <p class="text-sm text-slate-400 mt-1">
        管理和控制所有配置的机械臂
      </p>
    </div>

    <!-- 主内容区域 -->
    <div class="grid grid-cols-1 lg:grid-cols-4 gap-6">
      <!-- 左侧边栏: 机械臂列表 -->
      <div class="lg:col-span-1">
        <div class="bg-slate-900 rounded-lg p-4 border border-slate-800">
          <RobotListSelector
            :robots="displayRobots"
            :selected-robot-id="selectedRobotId"
            :is-loading="isLoading"
            :error-message="errorMessage"
            @update:selected-robot-id="handleRobotIdChange"
            @refresh="refresh"
            @select="handleRobotSelect"
          />
        </div>
      </div>

      <!-- 右侧主区域: 机械臂控制面板 -->
      <div class="lg:col-span-3">
        <div v-if="selectedRobot" class="bg-slate-900 rounded-lg p-6 border border-slate-800">
          <!-- 机械臂信息卡片 -->
          <div class="mb-6">
            <div class="flex items-center justify-between mb-4">
              <div>
                <h2 class="text-xl font-semibold text-slate-200">
                  {{ selectedRobot.id }}
                </h2>
                <p class="text-sm text-slate-400 mt-1">
                  {{ selectedRobot.mode === 'simulation' ? '仿真模式' : '实机模式' }}
                </p>
              </div>

              <!-- 状态徽章 -->
              <div class="flex items-center gap-2">
                <span
                  v-if="selectedRobot.connected"
                  class="px-3 py-1 rounded-full text-xs font-medium bg-green-500/20 text-green-400"
                >
                  已连接
                </span>
                <span
                  v-else
                  class="px-3 py-1 rounded-full text-xs font-medium bg-red-500/20 text-red-400"
                >
                  未连接
                </span>
              </div>
            </div>

            <!-- 状态网格 -->
            <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
              <StatusCard
                label="上电状态"
                :value="selectedRobot.powered ? '已上电' : '未上电'"
                :status="selectedRobot.powered ? 'success' : 'inactive'"
              />
              <StatusCard
                label="使能状态"
                :value="selectedRobot.enabled ? '已使能' : '未使能'"
                :status="selectedRobot.enabled ? 'success' : 'inactive'"
              />
              <StatusCard
                label="运动状态"
                :value="selectedRobot.moving ? '运动中' : '静止'"
                :status="selectedRobot.moving ? 'warning' : 'inactive'"
              />
              <StatusCard
                label="故障状态"
                :value="selectedRobot.faulted || selectedRobot.emergencyStopped ? '故障' : '正常'"
                :status="selectedRobot.faulted || selectedRobot.emergencyStopped ? 'error' : 'success'"
              />
            </div>
          </div>

          <!-- 控制权限检查 -->
          <div v-if="!selectedRobot.controlsEnabled" class="mb-6 p-4 bg-amber-500/10 border border-amber-500/30 rounded-lg">
            <div class="flex items-start gap-3">
              <svg class="w-5 h-5 text-amber-400 flex-shrink-0 mt-0.5" fill="currentColor" viewBox="0 0 20 20">
                <path fill-rule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clip-rule="evenodd"/>
              </svg>
              <div>
                <h3 class="text-sm font-medium text-amber-400">网页控制未启用</h3>
                <p class="text-xs text-slate-400 mt-1">
                  当前配置不允许通过网页控制此机械臂。请检查配置文件中的 <code class="px-1 py-0.5 bg-slate-800 rounded">web.jaka_controls_enabled</code> 设置。
                </p>
              </div>
            </div>
          </div>

          <!-- 机械臂控制面板 (这里可以集成现有的RobotControlPanel) -->
          <div v-if="selectedRobot.controlsEnabled">
            <h3 class="text-lg font-semibold text-slate-200 mb-4">控制面板</h3>
            <p class="text-sm text-slate-400">
              这里可以集成现有的 RobotControlPanel 组件或其他控制界面
            </p>
            <!-- <RobotControlPanel :robot-id="selectedRobot.id" /> -->
          </div>

          <!-- 错误信息显示 -->
          <div v-if="selectedRobot.lastError" class="mt-6 p-4 bg-red-500/10 border border-red-500/30 rounded-lg">
            <div class="flex items-start gap-3">
              <svg class="w-5 h-5 text-red-400 flex-shrink-0 mt-0.5" fill="currentColor" viewBox="0 0 20 20">
                <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clip-rule="evenodd"/>
              </svg>
              <div>
                <h3 class="text-sm font-medium text-red-400">错误</h3>
                <p class="text-xs text-slate-400 mt-1">{{ selectedRobot.lastError }}</p>
              </div>
            </div>
          </div>
        </div>

        <!-- 未选择机械臂时的空状态 -->
        <div v-else class="bg-slate-900 rounded-lg p-12 border border-slate-800 text-center">
          <svg class="w-16 h-16 mx-auto text-slate-700 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/>
          </svg>
          <h3 class="text-lg font-medium text-slate-400 mb-2">未选择机械臂</h3>
          <p class="text-sm text-slate-500">
            从左侧列表中选择一个机械臂以查看详细信息和控制选项
          </p>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount } from 'vue'
import { useRobotList } from '../composables/useRobotList'
import RobotListSelector from './RobotListSelector.vue'

// 状态卡片组件 (内联定义以简化示例)
const StatusCard = {
  props: {
    label: String,
    value: String,
    status: String,
  },
  template: `
    <div class="p-3 rounded-lg border" :class="statusClasses">
      <div class="text-xs text-slate-500 mb-1">{{ label }}</div>
      <div class="text-sm font-semibold" :class="valueClasses">{{ value }}</div>
    </div>
  `,
  computed: {
    statusClasses() {
      const status = this.status
      if (status === 'success') return 'bg-green-500/10 border-green-500/30'
      if (status === 'error') return 'bg-red-500/10 border-red-500/30'
      if (status === 'warning') return 'bg-amber-500/10 border-amber-500/30'
      return 'bg-slate-800 border-slate-700'
    },
    valueClasses() {
      const status = this.status
      if (status === 'success') return 'text-green-400'
      if (status === 'error') return 'text-red-400'
      if (status === 'warning') return 'text-amber-400'
      return 'text-slate-400'
    },
  },
}

// 使用机械臂列表composable
const {
  robots,
  selectedRobotId,
  selectedRobot,
  errorMessage,
  isLoading,
  start,
  stop,
  refresh,
  selectRobot,
} = useRobotList()

// 将robots转换为组件所需的格式
const displayRobots = computed(() =>
  robots.value.map((robot) => ({
    id: robot.id,
    mode: robot.mode,
    connected: robot.connected,
    powered: robot.powered,
    enabled: robot.enabled,
    moving: robot.moving,
    faulted: robot.faulted,
    emergencyStopped: robot.emergencyStopped,
    lastError: robot.lastError,
  }))
)

// 事件处理
function handleRobotIdChange(robotId: string | null) {
  if (robotId) {
    selectRobot(robotId)
  }
}

function handleRobotSelect(robotId: string) {
  console.log('Selected robot:', robotId)
  // 这里可以添加额外的逻辑，例如:
  // - 记录用户操作
  // - 更新URL参数
  // - 触发分析事件
}

// 生命周期
onMounted(() => {
  start()
})

onBeforeUnmount(() => {
  stop()
})
</script>
