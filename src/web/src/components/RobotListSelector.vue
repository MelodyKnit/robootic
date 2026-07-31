<template>
  <div class="space-y-4">
    <!-- 机械臂选择器 -->
    <div>
      <label class="block text-xs font-medium text-slate-400 mb-2">
        机械臂选择
      </label>
      <div class="flex gap-2">
        <select
          v-model="selectedRobotId"
          :disabled="isLoading || robots.length === 0"
          class="flex-1 bg-slate-800 border border-slate-700 rounded px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-blue-500 disabled:opacity-50 disabled:cursor-not-allowed"
          data-testid="robot-selector"
          @change="handleRobotChange"
        >
          <option v-if="robots.length === 0" :value="null">
            {{ isLoading ? '加载中...' : '无可用机械臂' }}
          </option>
          <option
            v-for="robot in robots"
            :key="robot.id"
            :value="robot.id"
          >
            {{ robot.id }} ({{ robot.mode === 'simulation' ? '仿真' : '实机' }})
          </option>
        </select>

        <button
          type="button"
          :disabled="isLoading"
          class="px-3 py-2 bg-slate-700 hover:bg-slate-600 disabled:bg-slate-800 disabled:opacity-50 rounded text-sm text-slate-200 transition-colors"
          title="刷新机械臂列表"
          @click="refresh"
        >
          <svg
            class="w-4 h-4"
            :class="{ 'animate-spin': isLoading }"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              stroke-linecap="round"
              stroke-linejoin="round"
              stroke-width="2"
              d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
            />
          </svg>
        </button>
      </div>

      <!-- 错误提示 -->
      <p
        v-if="errorMessage"
        class="mt-2 text-xs text-red-400"
      >
        {{ errorMessage }}
      </p>
    </div>

    <!-- 机械臂列表 -->
    <div
      v-if="robots.length > 0"
      class="space-y-2"
    >
      <h3 class="text-xs font-medium text-slate-400">
        可用机械臂 ({{ robots.length }})
      </h3>

      <div class="space-y-2">
        <button
          v-for="robot in robots"
          :key="robot.id"
          type="button"
          :class="[
            'w-full text-left px-4 py-3 rounded-lg border transition-all',
            selectedRobotId === robot.id
              ? 'bg-blue-500/10 border-blue-500'
              : 'bg-slate-800 border-slate-700 hover:border-slate-600',
          ]"
          @click="selectRobot(robot.id)"
        >
          <div class="flex items-center justify-between mb-2">
            <div class="flex items-center gap-2">
              <span class="font-medium text-slate-200">
                {{ robot.id }}
              </span>
              <span
                :class="[
                  'px-2 py-0.5 rounded text-xs',
                  robot.mode === 'simulation'
                    ? 'bg-purple-500/20 text-purple-400'
                    : 'bg-green-500/20 text-green-400',
                ]"
              >
                {{ robot.mode === 'simulation' ? '仿真' : '实机' }}
              </span>
            </div>

            <!-- 状态指示器 -->
            <div class="flex items-center gap-2">
              <StatusIndicator
                :connected="robot.connected"
                :powered="robot.powered"
                :enabled="robot.enabled"
                :faulted="robot.faulted"
                :emergency-stopped="robot.emergencyStopped"
              />
            </div>
          </div>

          <!-- 详细状态 -->
          <div class="grid grid-cols-2 gap-2 text-xs">
            <div>
              <span class="text-slate-500">连接:</span>
              <span :class="robot.connected ? 'text-green-400' : 'text-red-400'">
                {{ robot.connected ? '已连接' : '未连接' }}
              </span>
            </div>
            <div>
              <span class="text-slate-500">上电:</span>
              <span :class="robot.powered ? 'text-emerald-400' : 'text-slate-400'">
                {{ robot.powered ? '已上电' : '未上电' }}
              </span>
            </div>
            <div>
              <span class="text-slate-500">使能:</span>
              <span :class="robot.enabled ? 'text-blue-400' : 'text-slate-400'">
                {{ robot.enabled ? '已使能' : '未使能' }}
              </span>
            </div>
            <div>
              <span class="text-slate-500">运动:</span>
              <span :class="robot.moving ? 'text-amber-400' : 'text-slate-400'">
                {{ robot.moving ? '运动中' : '静止' }}
              </span>
            </div>
          </div>

          <!-- 错误信息 -->
          <div
            v-if="robot.lastError"
            class="mt-2 text-xs text-red-400"
          >
            {{ robot.lastError }}
          </div>
        </button>
      </div>
    </div>

    <!-- 空状态 -->
    <div
      v-else-if="!isLoading"
      class="text-center py-8 text-slate-500"
    >
      <svg
        class="w-12 h-12 mx-auto mb-3 text-slate-600"
        fill="none"
        stroke="currentColor"
        viewBox="0 0 24 24"
      >
        <path
          stroke-linecap="round"
          stroke-linejoin="round"
          stroke-width="2"
          d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"
        />
      </svg>
      <p class="text-sm">无可用机械臂</p>
      <p class="text-xs mt-1">请检查配置或启动机械臂服务</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { toRefs } from 'vue'
import StatusIndicator from './RobotStatusIndicator.vue'

interface Props {
  robots: Array<{
    id: string
    mode: 'simulation' | 'physical'
    connected: boolean
    powered: boolean
    enabled: boolean
    moving: boolean
    faulted: boolean
    emergencyStopped: boolean
    lastError: string | null
  }>
  selectedRobotId: string | null
  isLoading: boolean
  errorMessage: string | null
}

interface Emits {
  (event: 'update:selectedRobotId', value: string | null): void
  (event: 'refresh'): void
  (event: 'select', robotId: string): void
}

const props = defineProps<Props>()
const emit = defineEmits<Emits>()

const { selectedRobotId } = toRefs(props)

function handleRobotChange(event: Event): void {
  const target = event.target as HTMLSelectElement
  const value = target.value === 'null' ? null : target.value
  emit('update:selectedRobotId', value)
  if (value !== null) {
    emit('select', value)
  }
}

function refresh(): void {
  emit('refresh')
}

function selectRobot(robotId: string): void {
  emit('update:selectedRobotId', robotId)
  emit('select', robotId)
}
</script>
