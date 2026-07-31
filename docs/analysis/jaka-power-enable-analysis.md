# JAKA 机械臂上电和使能功能分析报告

## 📋 分析目标

根据用户需求，分析JAKA机械臂的上电（power_on）和使能（enable_robot）功能，评估是否适合在Web界面中提供远程操作能力。

**核心要求**：
- 必须契合JAKA的设计方式
- 深入理解参数和安全机制
- 不盲目为了完成任务而开发

---

## 🔍 SDK API分析

### 1. power_on() 方法

#### C++ API签名
```cpp
/**
 * @brief 打开机器人电源
 * @param handle  机器人控制句柄
 * @return ERR_SUCC 成功 其他失败
 */
errno_t power_on(const JKHD *handle);
```

#### Python API签名
```python
robot = jkrc.RC("192.168.2.160")
robot.login()
robot.power_on()  # 无参数，返回 errno_t 元组
```

**关键特征**：
- ✅ 无额外参数
- ✅ 返回错误码 (0表示成功，非0表示失败)
- ✅ 必须在login()之后调用
- ✅ SDK官方文档明确支持此功能

### 2. enable_robot() 方法

#### C++ API签名
```cpp
/**
 * @brief 控制机器人上使能
 * @param handle  机器人控制句柄
 * @return ERR_SUCC 成功 其他失败
 */
errno_t enable_robot(const JKHD *handle);
```

#### Python API签名
```python
robot.enable_robot()  # 无参数，返回 errno_t 元组
```

**关键特征**：
- ✅ 无额外参数
- ✅ 返回错误码
- ✅ 必须在power_on()之后调用
- ✅ 使能前控制器必须无故障、无急停、静止状态

### 3. 标准调用序列

根据SDK官方示例（joint_move.py等），标准序列为：
```python
robot = jkrc.RC("192.168.2.160")  # 创建连接
robot.login()                      # 登录
robot.power_on()                   # 上电
robot.enable_robot()               # 使能
# ... 执行运动命令 ...
robot.logout()                     # 登出
```

---

## 🛡️ 项目现有安全设计

### 1. adapter.py 的设计哲学

查看 `projects/gripper-ai-controller/src/gripper_ai_controller/adapters/jaka/adapter.py`:

```python
class JakaAdapter(BaseAdapter, RobotAdapter):
    """Normalize safe JAKA controller access through the project robot-adapter contract.

    Startup only opens an SDK session. ``execute`` deliberately rejects every real
    motion command so Runtime and plugins cannot move hardware. A separate narrow
    manual-control entry point accepts only an already-authorized blocking absolute
    joint move. Automatic controller power-on remains unsupported. Servo enable is an
    explicit operator action that requires a local opt-in and a powered, fault-free
    controller.
    """
```

**关键设计决策**：
1. ❌ **"Automatic controller power-on remains unsupported"** - 明确禁止自动上电
2. ✅ Enable是"explicit operator action" - 必须是操作员的显式动作
3. ✅ Enable需要"local opt-in" - 需要本地配置允许
4. ✅ Enable需要"powered, fault-free controller" - 需要已上电、无故障

### 2. enable() 方法的安全检查

```python
async def enable(self) -> RobotStatus:
    """Enable a powered, fault-free robot only after explicit local authorization.
    
    This method never calls `power_on()` and never sends a movement command. It is
    intentionally outside the runtime command path so planners cannot invoke it.
    """
    self.ensure_started()
    
    # 检查1: 本地配置必须明确允许
    if not self.allow_enable:
        raise PermissionError("JAKA enable is disabled until local configuration explicitly allows it.")
    
    # 检查2: 状态必须正常
    status = await self.get_status()
    if status.faulted or status.emergency_stopped:
        raise JakaAdapterError("JAKA enable refused because the controller reports a fault or emergency stop.")
    
    # 检查3: 必须静止
    if status.moving:
        raise JakaAdapterError("JAKA enable refused because the controller is not in-position.")
    
    # 检查4: 必须已上电（关键！）
    if not self._powered:
        raise JakaAdapterError("JAKA enable refused because the controller is not powered; automatic power-on is disabled.")
    
    # 检查5: 幂等性 - 已使能则直接返回
    if self._enabled:
        return status
    
    # 执行使能
    self.require_success("enable_robot", await self._call_client_method("enable_robot"))
    
    # 验证使能成功
    status = await self.get_status()
    if not self._enabled:
        raise JakaAdapterError("JAKA enable_robot returned success but telemetry is still not enabled.")
    
    return status
```

### 3. 为什么禁用power_on()？

从测试文件和设计文档可以看出：

**测试验证** (`test_jaka_adapter.py:335`):
```python
def test_enable_rejects_unpowered_or_faulted_controller_without_power_on(self):
    async def scenario():
        client = FakeJakaClient(powered=False)
        adapter = JakaAdapter("controller.test", allow_enable=True, client_factory=lambda _: client)
        await adapter.startup()
        with self.assertRaises(JakaAdapterError):
            await adapter.enable()
        self.assertNotIn("enable_robot", client.calls)
        self.assertNotIn("power_on", client.calls)  # ← 验证没有调用power_on
        await adapter.shutdown()
```

**设计原因**：
1. **安全性第一**：远程自动上电可能在不安全的情况下启动电机
2. **物理验证**：上电前操作员应该：
   - 确认工作区域无人员
   - 确认机械臂姿态安全
   - 确认无障碍物
   - 准备好急停按钮
3. **责任边界**：上电是改变物理硬件状态的关键操作，应该是操作员的主动决策
4. **故障预防**：避免程序自动上电导致的意外启动

---

## 🎯 Web界面实现可行性评估

### 方案A：仅实现enable按钮（当前项目设计）

**实现方式**：
- ✅ Web界面添加"使能"按钮
- ✅ 按钮仅在 `powered=true` 时可用
- ✅ 调用后端 `/api/robot/{id}/enable` 接口
- ✅ 后端调用现有的 `adapter.enable()` 方法

**优点**：
- ✅ 符合项目安全设计
- ✅ 使能前必须物理上电（操作员必须在现场）
- ✅ 代码已经过充分测试
- ✅ 责任边界清晰

**缺点**：
- ⚠️ 操作员需要分两步：物理上电 → Web使能
- ⚠️ Web界面无法完成完整启动流程

### 方案B：同时实现power_on和enable（需要修改设计）

**实现方式**：
- ⚠️ 添加 `allow_power_on` 配置选项
- ⚠️ 实现 `adapter.power_on()` 方法
- ⚠️ Web界面添加"上电"和"使能"两个按钮
- ⚠️ 上电按钮需要二次确认对话框

**优点**：
- ✅ Web界面可以完成完整启动流程
- ✅ 远程操作更方便

**风险**：
- ❌ 违背项目的"Automatic controller power-on remains unsupported"设计
- ❌ 远程上电可能在不安全的环境下执行
- ❌ 操作员可能没有物理验证工作区
- ❌ 没有物理按钮的触觉反馈
- ❌ 增加安全责任
- ❌ 测试用例会失败（需要修改多个测试）

### 方案C：混合模式（推荐）

**实现方式**：
- ✅ 默认禁用power_on（保持现有设计）
- ✅ 添加 `allow_remote_power_on` 配置（默认False）
- ✅ Web界面根据配置动态显示按钮
- ✅ 如果允许远程上电，需要：
  - 二次确认对话框
  - 显示安全检查清单
  - 记录操作日志
  - 5秒倒计时

**优点**：
- ✅ 保持默认安全行为
- ✅ 允许高级用户在受控环境下启用
- ✅ 灵活性最高
- ✅ 不破坏现有测试

**缺点**：
- ⚠️ 实现复杂度较高
- ⚠️ 需要完善的文档和警告

---

## 📊 对比表格

| 特性 | 方案A (仅Enable) | 方案B (Power+Enable) | 方案C (可配置) |
|------|-----------------|---------------------|---------------|
| 安全性 | ⭐⭐⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐⭐ |
| 便利性 | ⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ |
| 符合现有设计 | ⭐⭐⭐⭐⭐ | ⭐ | ⭐⭐⭐⭐ |
| 实现复杂度 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐ |
| 测试工作量 | ⭐⭐⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐⭐ |
| 责任边界清晰 | ⭐⭐⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐⭐ |

---

## 💡 推荐方案

### 推荐：方案C（可配置混合模式）

**理由**：
1. ✅ 尊重项目原有的安全设计哲学
2. ✅ 为实验室/受控环境提供便利选项
3. ✅ 通过配置开关控制风险
4. ✅ 不影响生产环境的默认安全行为

### 实现计划

#### Phase 1: 仅实现Enable按钮（1-2天）

**后端实现**：
```python
# 添加API端点
@router.post("/robots/{robot_id}/enable")
async def enable_robot(robot_id: str) -> RobotStatus:
    """使能已上电的机械臂"""
    adapter = get_robot_adapter(robot_id)
    return await adapter.enable()

@router.post("/robots/{robot_id}/disable")
async def disable_robot(robot_id: str) -> RobotStatus:
    """下使能机械臂"""
    adapter = get_robot_adapter(robot_id)
    return await adapter.disable()
```

**前端实现**：
```typescript
// RobotControlPanel.vue
<template>
  <div class="robot-control-panel">
    <button 
      :disabled="!canEnable"
      @click="handleEnable"
      class="btn-enable"
    >
      {{ status.enabled ? '已使能' : '使能' }}
    </button>
    
    <button 
      :disabled="!status.enabled"
      @click="handleDisable"
      class="btn-disable"
    >
      下使能
    </button>
  </div>
</template>

<script setup lang="ts">
const canEnable = computed(() => {
  return status.value.connected && 
         status.value.powered &&  // ← 关键：必须已上电
         !status.value.faulted && 
         !status.value.emergency_stopped &&
         !status.value.enabled
})
</script>
```

**安全提示UI**：
```vue
<div v-if="!status.powered" class="warning-box">
  ⚠️ 机械臂未上电。请在示教器或控制柜上手动上电后，再点击"使能"按钮。
</div>
```

#### Phase 2: 可选的Power-On功能（2-3天，可选）

**配置项**：
```yaml
# configs/local/jaka.yaml
jaka:
  controller_ip: "192.168.2.160"
  allow_enable: true
  allow_remote_power_on: false  # ← 新增，默认禁用
  allow_manual_motion: false
```

**后端修改**：
```python
class JakaAdapter(BaseAdapter, RobotAdapter):
    def __init__(
        self,
        controller_ip: str,
        allow_enable: bool = False,
        allow_remote_power_on: bool = False,  # ← 新增参数
        allow_manual_motion: bool = False,
        # ... 其他参数
    ):
        # ... 参数验证
        self.allow_remote_power_on = allow_remote_power_on
    
    async def power_on(self) -> RobotStatus:
        """上电控制器（需要显式配置允许）"""
        self.ensure_started()
        
        if not self.allow_remote_power_on:
            raise PermissionError(
                "JAKA remote power-on is disabled. "
                "Enable it in local config with 'allow_remote_power_on: true' "
                "only if you have verified the work area is clear and safe."
            )
        
        status = await self.get_status()
        if status.faulted or status.emergency_stopped:
            raise JakaAdapterError(
                "JAKA power-on refused due to fault or emergency stop. "
                "Clear the fault on the teach pendant first."
            )
        
        if self._powered:
            return status  # 幂等性
        
        self.require_success("power_on", await self._call_client_method("power_on"))
        
        status = await self.get_status()
        if not self._powered:
            raise JakaAdapterError("power_on returned success but telemetry shows not powered.")
        
        return status
```

**前端安全确认**：
```vue
<script setup lang="ts">
const handlePowerOn = async () => {
  // 安全检查清单对话框
  const confirmed = await showSafetyDialog({
    title: '⚠️ 上电确认',
    checklist: [
      '工作区域已清空，无人员',
      '机械臂姿态安全',
      '无障碍物遮挡运动空间',
      '急停按钮在可触及范围内',
      '已通知周围人员'
    ],
    countdown: 5,  // 5秒倒计时
    confirmText: '确认上电'
  })
  
  if (!confirmed) return
  
  try {
    await api.powerOnRobot(robotId)
    showSuccess('上电成功')
  } catch (error) {
    showError(`上电失败: ${error.message}`)
  }
}
</script>
```

---

## 📝 总结

### SDK层面
- ✅ `power_on()` 和 `enable_robot()` 都无额外参数
- ✅ 调用序列固定：login → power_on → enable_robot
- ✅ SDK本身支持远程上电

### 项目设计层面
- ⚠️ 项目有意禁用自动上电（安全考虑）
- ✅ Enable功能已实现且经过充分测试
- ✅ Enable要求控制器必须已上电

### 实现建议
1. **立即实现**：Enable按钮（方案A）
   - 低风险，符合现有设计
   - 满足基本使用需求
   
2. **可选实现**：Power-On按钮（方案C的扩展）
   - 仅在用户明确需要时实现
   - 必须通过配置显式启用
   - 需要完善的安全确认UI

3. **不推荐**：强制远程上电（方案B）
   - 违背项目安全哲学
   - 增加安全风险

---

## 🎬 下一步行动

请用户确认：
1. 是否先实现"使能"按钮（推荐，低风险）？
2. 是否需要"上电"按钮（需要配置开关和安全确认）？
3. 如果需要上电按钮，是否接受默认禁用、需要显式配置启用的设计？
