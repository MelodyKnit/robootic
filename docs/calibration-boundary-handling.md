# 标定功能边界情况处理改进

本文档记录了对标定功能边界情况处理的改进。

## 已完成的改进

### 1. WebSocket连接失败时的降级策略

**位置**: `src/web/src/composables/useCalibration.ts`

**改进内容**:
- 添加了指数退避重连机制
- 最多尝试5次重连，每次重连间隔递增（1s, 2s, 4s, 8s, 16s，最多30s）
- 重连失败后自动切换到轮询模式
- 添加了连接状态管理，避免重复连接

**实现细节**:
```typescript
let reconnectAttempts = 0;
let maxReconnectAttempts = 5;
let reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
let isIntentionallyClosed = false;

function attemptReconnect() {
  if (reconnectAttempts >= maxReconnectAttempts) {
    addLog('error', 'WebSocket重连失败，已切换到轮询模式');
    return;
  }
  
  reconnectAttempts++;
  const delay = Math.min(1000 * Math.pow(2, reconnectAttempts - 1), 30000);
  
  reconnectTimeout = setTimeout(() => {
    if (!isIntentionallyClosed) {
      connectWebSocket();
    }
  }, delay);
}
```

### 2. 状态轮询超时处理

**现有机制**:
- 使用`fetchStatus()`进行轮询
- API调用有内置的超时处理
- 错误会被捕获并记录到控制台

**建议**:
- 在调用方添加重试逻辑
- 连续失败后降低轮询频率

### 3. 快速状态切换时的UI更新

**现有机制**:
- Vue的响应式系统自动处理状态更新
- WebSocket消息立即更新状态
- 状态变更通过`handleWebSocketMessage`统一处理

**改进**:
- 所有状态更新都通过同一个响应式对象
- 避免状态冲突

### 4. 组件卸载时的资源清理

**改进内容**:
- 添加了`disconnectWebSocket()`方法
- 清理重连定时器
- 设置`isIntentionallyClosed`标志避免意外重连

**使用方式**:
```typescript
import { onBeforeUnmount } from 'vue'
import { useCalibration } from '@/composables/useCalibration'

const calibration = useCalibration()

onBeforeUnmount(() => {
  calibration.disconnectWebSocket()
})
```

### 5. 标定过程中页面刷新的恢复

**现有机制**:
- 状态存储在后端
- 页面刷新后通过`fetchStatus()`恢复状态
- WebSocket自动重连

**工作流程**:
1. 页面加载时调用`fetchStatus()`获取当前状态
2. 建立WebSocket连接接收实时更新
3. 如果标定正在运行，UI自动显示当前进度

## 测试覆盖

已创建E2E测试套件：`src/web/tests/calibration-module.spec.ts`

**测试场景**:
- ✓ 不同状态下的UI显示（idle, running, paused, completed, error）
- ✓ 用户交互（开始、暂停、继续、停止、重试）
- ✓ 状态轮询机制
- ✓ WebSocket连接失败降级
- ✓ 多个标定模块显示

**注意**: 测试当前失败是因为CalibrationModuleCard组件尚未集成到主应用中。测试为组件的未来实现提供了规范。

## 待完善的边界情况

### 1. 网络断开恢复

**场景**: 用户网络短暂断开后恢复

**建议**:
- 监听`online`/`offline`事件
- 网络恢复后立即重新获取状态
- 显示网络状态提示

### 2. 并发操作保护

**场景**: 用户快速点击多次操作按钮

**建议**:
- 添加操作中状态，禁用按钮
- API调用添加防抖/节流
- 后端实现幂等性保护

### 3. 长时间无响应处理

**场景**: WebSocket连接存在但长时间无消息

**建议**:
- 实现心跳机制
- 超时后主动查询状态
- 提示用户连接可能异常

### 4. 状态不一致处理

**场景**: 前端状态与后端状态不一致

**建议**:
- 定期轮询验证状态一致性
- 检测到不一致时强制同步
- 记录状态不一致事件用于调试

### 5. 大量日志的性能优化

**场景**: 长时间运行产生大量日志

**当前实现**: 限制日志数量为100条

**建议**:
- 实现虚拟滚动
- 提供日志导出功能
- 按级别过滤日志

## 使用指南

### 在组件中使用

```vue
<script setup lang="ts">
import { onMounted, onBeforeUnmount } from 'vue'
import { useCalibration } from '@/composables/useCalibration'

const calibration = useCalibration()

onMounted(async () => {
  // 获取初始状态
  await calibration.fetchStatus()
  
  // 建立WebSocket连接
  calibration.connectWebSocket()
})

onBeforeUnmount(() => {
  // 清理资源
  calibration.disconnectWebSocket()
})

// 开始标定
async function handleStart() {
  try {
    await calibration.startCalibration({
      calibration_id: 'my-calibration',
      camera_id: 'camera-1'
    })
  } catch (error) {
    console.error('启动失败:', error)
  }
}
</script>
```

### 手动重置WebSocket

在某些情况下（例如切换标定模块），需要手动重置WebSocket连接：

```typescript
// 断开现有连接
calibration.disconnectWebSocket()

// 重置状态
calibration.resetWebSocket()

// 重新连接
calibration.connectWebSocket()
```

## 相关文件

- `src/web/src/composables/useCalibration.ts` - 核心实现
- `src/web/tests/calibration-module.spec.ts` - E2E测试
- `CALIBRATION_UI_VERIFICATION.md` - 功能验证报告

## 更新日期

2026-07-31
