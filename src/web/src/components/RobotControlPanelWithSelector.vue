<template>
  <div class="space-y-4">
    <!-- 机械臂选择器 (当有多个机械臂时显示) -->
    <div v-if="showRobotSelector" class="bg-slate-800 border border-slate-700 rounded-lg p-4">
      <h3 class="text-sm font-medium text-slate-300 mb-3">机械臂选择</h3>
      <RobotListSelector
        :robots="availableRobots"
        :selected-robot-id="selectedRobotId"
        :is-loading="isLoadingList"
        :error-message="listErrorMessage"
        @update:selected-robot-id="handleRobotSelection"
        @refresh="refreshRobotList"
        @select="handleRobotSelect"
      />
    </div>

    <!-- 现有的机械臂控制面板内容 -->
    <div v-if="selectedRobotStatus" class="space-y-4">
      <!-- 机械臂信息卡片 -->
      <div class="bg-slate-800 border border-slate-700 rounded-lg p-4">
        <div class="flex items-center justify-between mb-3">
          <div>
            <h3 class="text-lg font-semibold text-slate-200">
              {{ selectedRobotStatus.id }}
            </h3>
            <p class="text-xs text-slate-400 mt-0.5">
              {{ selectedRobotStatus.mode === 'simulation' ? '仿真模式' : '实机模式' }}
            </p>
          </div>
          <RobotStatusIndicator
            :connected="selectedRobotStatus.connected"
            :powered="selectedRobotStatus.powered"
            :enabled="selectedRobotStatus.enabled"
            :faulted="selectedRobotStatus.faulted"
            :emergency-stopped="selectedRobotStatus.emergencyStopped"
          />
        </div>

        <!-- 快速状态 -->
        <div class="grid grid-cols-4 gap-2 text-xs">
          <div>
            <span class="text-slate-500">连接:</span>
            <span :class="selectedRobotStatus.connected ? 'text-green-400' : 'text-red-400'">
              {{ selectedRobotStatus.connected ? '已连接' : '未连接' }}
            </span>
          </div>
          <div>
            <span class="text-slate-500">上电:</span>
            <span :class="selectedRobotStatus.powered ? 'text-emerald-400' : 'text-slate-400'">
              {{ selectedRobotStatus.powered ? '已上电' : '未上电' }}
            </span>
          </div>
          <div>
            <span class="text-slate-500">使能:</span>
            <span :class="selectedRobotStatus.enabled ? 'text-blue-400' : 'text-slate-400'">
              {{ selectedRobotStatus.enabled ? '已使能' : '未使能' }}
            </span>
          </div>
          <div>
            <span class="text-slate-500">运动:</span>
            <span :class="selectedRobotStatus.moving ? 'text-amber-400' : 'text-slate-400'">
              {{ selectedRobotStatus.moving ? '运动中' : '静止' }}
            </span>
          </div>
        </div>

        <!-- 错误信息 -->
        <div
          v-if="selectedRobotStatus.lastError"
          class="mt-3 p-2 bg-red-500/10 border border-red-500/30 rounded text-xs text-red-400"
        >
          {{ selectedRobotStatus.lastError }}
        </div>
      </div>

      <!-- 原有的RobotControlPanel内容将在这里继续 -->
      <slot name="control-content" :robot="selectedRobotStatus" />
    </div>

    <!-- 无可用机械臂时的提示 -->
    <div v-else-if="!isLoadingList" class="bg-slate-800 border border-slate-700 rounded-lg p-8 text-center">
      <svg class="w-12 h-12 mx-auto text-slate-600 mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/>
      </svg>
      <p class="text-sm text-slate-400">
        {{ listErrorMessage || '无可用机械臂' }}
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount } from 'vue'
import { useRobotList } from '../composables/useRobotList'
import RobotListSelector from './RobotListSelector.vue'
import RobotStatusIndicator from './RobotStatusIndicator.vue'

// 使用机械臂列表管理
const {
  robots: availableRobots,
  selectedRobotId,
  selectedRobot: selectedRobotStatus,
  errorMessage: listErrorMessage,
  isLoading: isLoadingList,
  start: startRobotList,
  stop: stopRobotList,
  refresh: refreshRobotList,
  selectRobot,
} = useRobotList()

// 当有多个机械臂时显示选择器
const showRobotSelector = computed(() => availableRobots.value.length > 1)

// 事件处理
function handleRobotSelection(robotId: string | null) {
  if (robotId) {
    selectRobot(robotId)
  }
}

function handleRobotSelect(robotId: string) {
  console.log('用户选择了机械臂:', robotId)
  // 可以在这里添加额外的逻辑，例如:
  // - 保存用户偏好
  // - 记录操作日志
  // - 更新URL参数
}

// 生命周期管理
onMounted(() => {
  startRobotList()
})

onBeforeUnmount(() => {
  stopRobotList()
})

// 导出选中的机械臂ID供外部使用
defineExpose({
  selectedRobotId,
  selectedRobotStatus,
})
</script>
