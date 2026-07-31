# Web界面机械臂列表功能 - 快速开始

> 本指南帮助您快速上手新增的机械臂列表功能

## 🚀 快速开始

### 1. 基本使用

```vue
<script setup lang="ts">
import { onMounted, onBeforeUnmount } from 'vue'
import { useRobotList } from '@/composables/useRobotList'
import RobotListSelector from '@/components/RobotListSelector.vue'

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

onMounted(() => start())
onBeforeUnmount(() => stop())
</script>

<template>
  <RobotListSelector
    :robots="robots"
    :selected-robot-id="selectedRobotId"
    :is-loading="isLoading"
    :error-message="errorMessage"
    @update:selected-robot-id="selectRobot"
    @refresh="refresh"
  />
</template>
```

### 2. 使用现成的集成组件

如果您想要一个开箱即用的解决方案：

```vue
<script setup lang="ts">
import RobotControlPanelWithSelector from '@/components/RobotControlPanelWithSelector.vue'
</script>

<template>
  <RobotControlPanelWithSelector>
    <template #control-content="{ robot }">
      <!-- 在这里添加您的控制界面 -->
      <div>控制 {{ robot.id }}</div>
    </template>
  </RobotControlPanelWithSelector>
</template>
```

### 3. 查看完整示例

参考 `src/web/src/components/RobotManagementExample.vue` 查看完整的页面实现示例。

## 📚 文档索引

### 核心文档
- **[完整实现报告](./ROBOT_LIST_COMPLETE_REPORT.md)** - 查看所有实现细节
- **[集成指南](./docs/robot-list-integration.md)** - 详细的集成说明和代码示例
- **[实现总结](./docs/robot-list-implementation-summary.md)** - 技术实现和架构说明

### API参考

#### useRobotList Composable

```typescript
interface UseRobotList {
  // 状态
  robots: Ref<RobotStatus[]>
  selectedRobotId: Ref<string | null>
  selectedRobot: ComputedRef<RobotStatus | null>
  errorMessage: Ref<string | null>
  isLoading: Ref<boolean>
  
  // 方法
  start: () => Promise<void>
  stop: () => void
  refresh: () => Promise<void>
  selectRobot: (robotId: string | null) => void
}
```

#### RobotListSelector Props

```typescript
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
```

#### RobotListSelector Events

```typescript
// 选择变更（支持v-model）
emit('update:selectedRobotId', robotId: string | null)

// 刷新请求
emit('refresh')

// 机械臂被选中
emit('select', robotId: string)
```

## 🧪 测试

### 运行E2E测试

```bash
cd src/web
pnpm test:e2e robot-list.spec.ts
```

### TypeScript类型检查

```bash
cd src/web
pnpm typecheck
```

### 构建验证

```bash
cd src/web
pnpm build
```

## 🛠️ 集成方式

### 方式1: 独立页面

创建一个专门的机械臂管理页面，提供完整的列表和控制功能。

**适用场景**: 需要集中管理多个机械臂

**参考**: `RobotManagementExample.vue`

### 方式2: 集成到现有控制面板

在现有的RobotControlPanel中添加机械臂选择器。

**适用场景**: 增强现有功能，支持多机械臂切换

**参考**: `RobotControlPanelWithSelector.vue`

### 方式3: 硬件适配器区域

在HardwareAdapterSection中添加机械臂列表标签页。

**适用场景**: 统一的硬件管理界面

**参考**: 集成指南中的方案3

## 🎯 核心功能

- ✅ 自动发现所有配置的机械臂
- ✅ 实时状态监控（连接、上电、使能、运动）
- ✅ 自动轮询刷新（2秒间隔）
- ✅ 机械臂选择和切换
- ✅ 仿真/实机模式区分
- ✅ 错误提示和空状态处理
- ✅ 响应式UI设计

## 🎨 UI组件

### RobotListSelector
主要的机械臂列表选择器组件，包含：
- 下拉选择器
- 卡片列表视图
- 状态显示
- 刷新按钮

### RobotStatusIndicator
状态指示器组件，使用彩色圆点显示：
- 🟢 已连接
- 🟢 已上电
- 🔵 已使能
- 🔴 故障/急停

### RobotControlPanelWithSelector
集成了列表选择器的控制面板组件，提供：
- 智能显示（单机械臂时隐藏选择器）
- 机械臂信息卡片
- 状态快速查看
- 可扩展的控制内容插槽

## 📊 状态说明

### 机械臂模式
- `simulation` - 仿真模式
- `physical` - 实机模式

### 状态标志
- `connected` - 是否已连接
- `powered` - 是否已上电
- `enabled` - 是否已使能
- `moving` - 是否正在运动
- `faulted` - 是否故障
- `emergencyStopped` - 是否急停

## 🔧 常见问题

### Q: 机械臂列表为空？
A: 检查后端服务是否启动，配置文件中是否定义了机械臂。

### Q: 状态不更新？
A: 确保在`onMounted`中调用了`start()`，在`onBeforeUnmount`中调用了`stop()`。

### Q: 如何自定义轮询间隔？
A: 目前轮询间隔固定为2秒。如需修改，可以编辑`useRobotList.ts`中的`statusRefreshMilliseconds`常量。

### Q: 支持WebSocket吗？
A: 当前使用HTTP轮询。WebSocket支持已列入未来改进计划。

更多问题请查看 [集成指南](./docs/robot-list-integration.md) 中的故障排查章节。

## 📁 文件结构

```
src/web/src/
├── composables/
│   └── useRobotList.ts                      # 机械臂列表逻辑
├── components/
│   ├── RobotListSelector.vue                # 列表选择器组件
│   ├── RobotStatusIndicator.vue             # 状态指示器
│   ├── RobotControlPanelWithSelector.vue    # 集成控制面板
│   └── RobotManagementExample.vue           # 完整示例
└── api/
    └── robot.ts                             # API接口（已存在）
```

## 🎓 最佳实践

1. **资源清理**: 始终在`onBeforeUnmount`中调用`stop()`
2. **错误处理**: 显示错误消息给用户
3. **加载状态**: 使用`isLoading`显示加载指示器
4. **自动选择**: 如果只有一个机械臂，会自动选中
5. **状态同步**: 确保UI状态与实际机械臂状态一致

## 🚀 生产就绪

- ✅ TypeScript类型检查通过
- ✅ 生产构建成功
- ✅ E2E测试覆盖
- ✅ 文档完善
- ✅ 代码质量高

## 📞 获取帮助

- 查看 [完整实现报告](./ROBOT_LIST_COMPLETE_REPORT.md)
- 查看 [集成指南](./docs/robot-list-integration.md)
- 查看示例代码 `RobotManagementExample.vue`

---

**祝您使用愉快！** 🎉
