# 标定功能使用指南

本文档提供标定功能的完整使用说明，包括前置条件、操作流程、API文档和故障排查。

## 目录

- [前置条件](#前置条件)
- [操作流程](#操作流程)
- [CalibrationModuleCard组件](#calibrationmodulecard组件)
- [标定服务集成](#标定服务集成)
- [故障排查指南](#故障排查指南)

---

## 前置条件

### 硬件要求

1. **相机**
   - 相机已正确连接并能够采集图像
   - 相机已配置并可在预览界面看到实时画面
   - 建议使用固定安装的相机以获得稳定的标定结果

2. **标定板**
   - 棋盘格标定板（推荐9x6或10x7）
   - 标定板平整无变形
   - 标定板尺寸已知（单个方格的物理尺寸，单位：毫米）

3. **照明环境**
   - 光照均匀，避免强烈反光
   - 标定板对比度清晰
   - 避免阴影遮挡标定板

### 软件要求

1. **后端服务**
   - 标定服务已启动并运行正常
   - WebSocket服务可用（推荐，用于实时状态更新）
   - 相机适配器已正确配置

2. **前端配置**
   - 浏览器支持WebSocket（现代浏览器均支持）
   - 网络连接稳定
   - 建议使用Chrome、Edge或Firefox最新版本

---

## 操作流程

### 1. 相机内参标定

相机内参标定用于获取相机的内部参数（焦距、主点、畸变系数等）。

#### 准备工作

1. 确认相机预览正常工作
2. 准备标定板并记录其参数：
   - 棋盘格内角点数量（例如：9x6）
   - 单个方格边长（例如：25mm）

#### 标定步骤

1. **打开标定界面**
   - 在主界面找到"标定"或"Calibration"选项
   - 选择"相机内参标定"模块

2. **配置标定参数**
   - 棋盘格列数（Columns）：内角点列数
   - 棋盘格行数（Rows）：内角点行数
   - 方格尺寸（Square Size）：单个方格边长（mm）
   - 目标图像数量：建议20-30张

3. **开始采集**
   - 点击"开始标定"按钮
   - 按照提示移动标定板到不同位置和角度
   - 系统会自动检测标定板并采集图像
   - 观察实时反馈：
     - 绿色提示：成功检测并采集
     - 橙色提示：未检测到标定板或质量不佳
     - 进度条显示当前采集进度

4. **采集注意事项**
   - 覆盖相机视野的不同区域（中心、边缘、角落）
   - 包含不同角度（正面、倾斜、旋转）
   - 确保标定板完全在视野内
   - 标定板边缘清晰可见
   - 避免运动模糊（保持稳定）

5. **查看结果**
   - 采集完成后，系统自动进行计算
   - 查看标定结果：
     - 重投影误差（RMS Error）：通常应小于0.5像素
     - 相机矩阵（Camera Matrix）
     - 畸变系数（Distortion Coefficients）
   - 结果自动保存，并分配唯一ID

6. **应用标定**
   - 在结果列表中选择要应用的标定
   - 点击"应用"按钮
   - 确认后，标定参数将用于后续的视觉处理

### 2. 手眼标定

手眼标定用于确定相机与机械臂基座或工具端的空间关系。

#### Eye-in-Hand（工具端相机）

相机安装在机械臂工具端，随机械臂移动。

**准备工作**:
1. 相机已完成内参标定
2. 机械臂可正常控制
3. 标定板固定在工作空间内（不移动）

**标定步骤**:
1. 选择"手眼标定（Eye-in-Hand）"模块
2. 配置标定板参数
3. 开始标定
4. 控制机械臂移动到多个位置（建议10-15个）
5. 每个位置确保：
   - 标定板在相机视野内
   - 标定板清晰可见
   - 机械臂位姿已稳定
6. 系统自动采集并计算
7. 查看标定结果和精度评估

#### Eye-to-Hand（固定相机）

相机固定在工作空间，机械臂在其视野内移动。

**准备工作**:
1. 相机已完成内参标定
2. 机械臂可正常控制
3. 标定板固定在机械臂工具端

**标定步骤**:
1. 选择"手眼标定（Eye-to-Hand）"模块
2. 配置标定板参数
3. 开始标定
4. 控制机械臂移动标定板到多个位置
5. 每个位置的要求同上
6. 系统自动采集并计算
7. 查看标定结果和精度评估

---

## CalibrationModuleCard组件

### 组件概述

`CalibrationModuleCard`是标定模块的通用卡片组件，用于显示标定状态和提供操作控制。

### Props

```typescript
interface CalibrationModule {
  module_id: string              // 模块唯一标识
  name: string                   // 模块名称
  description: string            // 模块描述
  state: 'idle' | 'running' | 'paused' | 'completed' | 'error'
  progress: {
    current: number              // 当前进度
    total: number                // 总进度
  }
  error: string | null           // 错误消息
}

interface Props {
  module: CalibrationModule
}
```

### Events

```typescript
// 开始标定
emit('start', moduleId: string)

// 暂停标定
emit('pause', moduleId: string)

// 继续标定
emit('resume', moduleId: string)

// 停止标定
emit('stop', moduleId: string)
```

### 使用示例

```vue
<template>
  <CalibrationModuleCard
    :module="handEyeModule"
    @start="handleStart"
    @pause="handlePause"
    @resume="handleResume"
    @stop="handleStop"
  />
</template>

<script setup lang="ts">
import { ref } from 'vue'
import CalibrationModuleCard from '@/components/CalibrationModuleCard.vue'

const handEyeModule = ref({
  module_id: 'hand-eye',
  name: '手眼标定',
  description: '标定机械臂工具端相机与基座的空间关系',
  state: 'idle',
  progress: { current: 0, total: 10 },
  error: null,
})

async function handleStart(moduleId: string) {
  const response = await fetch(`/api/calibration/modules/${moduleId}/start`, {
    method: 'POST',
  })
  // 处理响应
}

async function handlePause(moduleId: string) {
  const response = await fetch(`/api/calibration/modules/${moduleId}/pause`, {
    method: 'POST',
  })
  // 处理响应
}

async function handleResume(moduleId: string) {
  const response = await fetch(`/api/calibration/modules/${moduleId}/resume`, {
    method: 'POST',
  })
  // 处理响应
}

async function handleStop(moduleId: string) {
  const response = await fetch(`/api/calibration/modules/${moduleId}/stop`, {
    method: 'POST',
  })
  // 处理响应
}
</script>
```

### 状态显示

| State | 显示文本 | 颜色 | 可用操作 |
|-------|---------|------|---------|
| idle | 未开始 | 灰色 | 开始 |
| running | 运行中 | 蓝色 | 暂停、停止 |
| paused | 已暂停 | 黄色 | 继续、停止 |
| completed | 已完成 | 绿色 | 重新开始 |
| error | 错误 | 红色 | 重试 |

---

## 标定服务集成

### API端点

#### 1. 获取标定模块列表

```http
GET /api/calibration/modules
```

**响应**:
```json
{
  "modules": [
    {
      "module_id": "intrinsic",
      "name": "相机内参标定",
      "description": "标定相机的内部参数",
      "state": "idle",
      "progress": { "current": 0, "total": 30 },
      "error": null
    },
    {
      "module_id": "hand-eye",
      "name": "手眼标定",
      "description": "标定机械臂工具端相机与基座的空间关系",
      "state": "idle",
      "progress": { "current": 0, "total": 10 },
      "error": null
    }
  ]
}
```

#### 2. 启动标定

```http
POST /api/calibration/modules/{module_id}/start
Content-Type: application/json

{
  "calibration_id": "string",
  "camera_id": "string",
  "config": {
    "board_width": 9,
    "board_height": 6,
    "square_size_mm": 25.0,
    "target_images": 30
  }
}
```

#### 3. 暂停标定

```http
POST /api/calibration/modules/{module_id}/pause
```

#### 4. 继续标定

```http
POST /api/calibration/modules/{module_id}/resume
```

#### 5. 停止标定

```http
POST /api/calibration/modules/{module_id}/stop
```

#### 6. 获取标定状态

```http
GET /api/calibration/modules/{module_id}/status
```

#### 7. WebSocket实时更新

```
ws://localhost:8000/api/calibration/ws
```

**消息类型**:

```typescript
// 进度更新
{
  "type": "calibration.progress",
  "data": {
    "module_id": "intrinsic",
    "state": "running",
    "progress": { "current": 5, "total": 30 }
  }
}

// 位姿完成
{
  "type": "calibration.pose_complete",
  "data": {
    "module_id": "hand-eye",
    "pose_index": 3,
    "success": true,
    "corner_count": 54,
    "description": "位姿 4/10"
  }
}

// 阶段完成
{
  "type": "calibration.phase_complete",
  "data": {
    "module_id": "intrinsic",
    "phase": "采集",
    "result": {
      "rms_error": 0.32,
      "camera_matrix": [[...], [...], [...]],
      "distortion_coefficients": [...]
    }
  }
}

// 错误
{
  "type": "calibration.error",
  "data": {
    "module_id": "intrinsic",
    "message": "相机连接失败"
  }
}

// 状态变更
{
  "type": "calibration.state_changed",
  "data": {
    "module_id": "intrinsic",
    "state": "paused"
  }
}
```

### Composable集成

```typescript
import { useCalibration } from '@/composables/useCalibration'

const calibration = useCalibration()

// 生命周期
onMounted(async () => {
  await calibration.fetchStatus()
  calibration.connectWebSocket()
})

onBeforeUnmount(() => {
  calibration.disconnectWebSocket()
})

// 操作
await calibration.startCalibration({
  calibration_id: 'my-calibration',
  camera_id: 'camera-1',
  config: { ... }
})

await calibration.pauseCalibration()
await calibration.resumeCalibration()
await calibration.stopCalibration()

// 状态
const isRunning = calibration.isRunning
const status = calibration.status
const logs = calibration.logs
```

---

## 故障排查指南

### 问题1：无法检测到标定板

**症状**:
- 标定过程中一直显示"未检测到标定板"
- 采集进度长时间无变化

**可能原因**:
1. 标定板配置参数错误（行列数、方格尺寸）
2. 标定板不在相机视野内或太小
3. 光照条件不佳，对比度不足
4. 标定板变形或破损
5. 相机对焦不准确

**解决方法**:
1. 检查标定板参数配置是否与实际标定板匹配
2. 调整标定板位置，确保完全在视野内
3. 改善光照条件，避免反光和阴影
4. 使用平整的标定板
5. 调整相机对焦，确保标定板清晰可见
6. 查看相机预览，确认标定板清晰度

### 问题2：标定精度不佳

**症状**:
- 重投影误差（RMS Error）> 1.0像素
- 标定结果应用后视觉识别不准确

**可能原因**:
1. 采集图像数量不足
2. 采集位置覆盖不全面
3. 标定板在采集时有运动模糊
4. 相机本身畸变严重
5. 标定板制作不精确

**解决方法**:
1. 增加采集图像数量（建议25-30张）
2. 确保覆盖视野的所有区域：
   - 中心、四个角落
   - 四条边缘的中点
   - 不同倾斜角度
3. 采集时保持稳定，避免晃动
4. 考虑使用更高精度的标定板
5. 重新标定，剔除质量差的图像

### 问题3：WebSocket连接失败

**症状**:
- 控制台显示"WebSocket连接错误"
- 状态更新延迟或不更新
- 日志显示"WebSocket已断开，尝试重连..."

**可能原因**:
1. 后端WebSocket服务未启动
2. 网络连接不稳定
3. 防火墙或代理阻止WebSocket连接
4. 服务器负载过高

**解决方法**:
1. 检查后端服务是否正常运行
2. 查看浏览器开发者工具的网络选项卡，确认WebSocket连接状态
3. 检查防火墙设置，允许WebSocket连接
4. 系统会自动尝试重连（最多5次）
5. 重连失败后，系统自动切换到轮询模式
6. 刷新页面重新建立连接

### 问题4：标定过程中断

**症状**:
- 标定突然停止
- 状态显示为"错误"
- 出现错误消息

**可能原因**:
1. 相机连接断开
2. 后端服务崩溃或重启
3. 磁盘空间不足
4. 内存不足

**解决方法**:
1. 检查相机连接状态
2. 重启后端服务
3. 检查磁盘空间，清理不必要的文件
4. 检查服务器日志获取详细错误信息
5. 点击"重试"按钮继续标定
6. 如果无法恢复，重新开始标定

### 问题5：操作按钮无响应

**症状**:
- 点击"开始"、"暂停"等按钮无反应
- 按钮一直显示加载状态

**可能原因**:
1. API请求超时
2. 后端服务未响应
3. 网络连接问题
4. 浏览器JavaScript错误

**解决方法**:
1. 打开浏览器开发者工具查看控制台错误
2. 检查网络选项卡，确认API请求状态
3. 刷新页面重试
4. 检查后端服务日志
5. 使用浏览器的硬刷新（Ctrl+Shift+R）清除缓存

### 问题6：标定结果无法保存

**症状**:
- 标定完成但结果列表中找不到
- 提示"保存失败"

**可能原因**:
1. 磁盘空间不足
2. 文件权限问题
3. 数据库连接失败
4. 配置文件路径错误

**解决方法**:
1. 检查磁盘空间
2. 检查后端服务对标定结果目录的写权限
3. 查看后端日志获取详细错误信息
4. 检查配置文件中的标定结果存储路径

### 调试技巧

1. **查看浏览器控制台**
   - 按F12打开开发者工具
   - 查看Console选项卡的错误和警告信息
   - 查看Network选项卡的API请求和响应

2. **查看后端日志**
   - 标定服务的日志文件位置：`logs/calibration.log`
   - 使用`tail -f logs/calibration.log`实时查看日志

3. **测试API端点**
   - 使用curl或Postman测试API是否正常响应
   - 示例：`curl http://localhost:8000/api/calibration/modules`

4. **检查WebSocket连接**
   - 浏览器开发者工具 → Network → WS
   - 查看WebSocket消息的发送和接收

5. **验证标定板检测**
   - 在相机预览界面观察实时画面
   - 确认标定板在视野内且清晰可见

## 最佳实践

1. **标定前准备**
   - 预热相机10-15分钟，确保稳定性
   - 检查标定板质量，确保平整无损
   - 准备充足的时间，避免匆忙操作

2. **采集策略**
   - 先采集中心区域，再采集边缘区域
   - 包含多种倾斜角度（15°、30°、45°）
   - 每个位置保持2-3秒稳定再移动

3. **质量验证**
   - 重投影误差 < 0.5像素为优秀
   - 0.5-1.0像素为可接受
   - > 1.0像素需要重新标定

4. **结果管理**
   - 为每次标定添加有意义的ID（包含日期、相机名称）
   - 定期备份标定结果
   - 记录标定时的环境条件（光照、温度等）

5. **维护建议**
   - 相机或光学组件更换后需重新标定
   - 建议每3-6个月验证标定精度
   - 重大环境变化后考虑重新标定

## 相关文档

- [标定功能边界处理](./calibration-boundary-handling.md)
- [标定UI集成验证报告](../CALIBRATION_UI_VERIFICATION.md)
- [配置文件结构说明](../configs/README.md)

## 更新历史

- 2026-07-31: 初始版本，包含完整使用指南和故障排查

---

如有问题或建议，请查看项目文档或联系开发团队。
