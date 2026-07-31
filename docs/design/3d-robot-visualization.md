# JAKA机械臂3D可视化实施计划

## 📋 项目目标

在Web界面中实现JAKA Zu3机械臂的3D实时可视化，显示当前关节角度并实时跟随真实设备运动。

---

## 🎯 核心需求

### 功能需求
1. **3D模型渲染**：显示JAKA Zu3机械臂的3D模型
2. **实时同步**：根据后端返回的关节角度实时更新模型姿态
3. **交互操作**：
   - 鼠标旋转、缩放、平移视角
   - 显示关节角度标注
   - 高亮当前运动的关节
4. **性能要求**：流畅的60fps渲染，延迟<100ms

### 非功能需求
- 轻量级：不影响现有Web界面性能
- 可维护：清晰的代码结构
- 可扩展：未来支持轨迹预览、碰撞检测

---

## 🔍 技术方案调研

### 方案1：Three.js + URDF Loader（推荐）

#### 技术栈
- **Three.js**：WebGL 3D渲染库
- **urdf-loader**：加载机器人URDF模型
- **react-three-fiber**（可选）：React集成

#### 优势
✅ 成熟稳定，社区活跃
✅ 支持URDF标准格式（机器人建模标准）
✅ 性能优秀，支持硬件加速
✅ 丰富的生态系统（lights, shadows, post-processing）
✅ 与Vue 3兼容性好

#### 劣势
⚠️ 需要获取JAKA Zu3的URDF模型文件
⚠️ 包体积较大（~600KB gzipped）

#### 实现复杂度
⭐⭐⭐ 中等

#### 示例项目
- [ros3djs](https://github.com/RobotWebTools/ros3djs) - ROS机器人可视化
- [urdf-loader examples](https://github.com/gkjohnson/urdf-loaders)

---

### 方案2：Babylon.js

#### 技术栈
- **Babylon.js**：完整的游戏引擎级3D框架
- 内置物理引擎、粒子系统

#### 优势
✅ 功能更强大，开箱即用
✅ 内置调试工具（Inspector）
✅ 性能优化更好
✅ 官方提供Vue集成

#### 劣势
⚠️ 包体积更大（~1.2MB gzipped）
⚠️ 学习曲线陡峭
⚠️ 对URDF支持不如Three.js

#### 实现复杂度
⭐⭐⭐⭐ 较高

---

### 方案3：A-Frame（不推荐）

#### 技术栈
- **A-Frame**：基于HTML的WebVR框架

#### 优势
✅ 声明式开发，易上手
✅ VR/AR支持

#### 劣势
❌ 不适合工业控制场景
❌ 灵活性差
❌ 性能不如Three.js

---

### 方案4：自研简单渲染器（不推荐）

#### 优势
✅ 包体积最小
✅ 完全可控

#### 劣势
❌ 开发成本极高
❌ 无法应对复杂需求
❌ 维护困难

---

## 🏆 推荐方案：Three.js + urdf-loader

### 理由
1. **行业标准**：URDF是机器人建模的标准格式
2. **成熟生态**：Three.js是WebGL事实标准
3. **平衡性好**：功能、性能、体积的最佳平衡
4. **可扩展性**：轻松添加轨迹预览、碰撞检测等高级功能

---

## 📐 架构设计

### 前端架构

```
src/web/src/
├── components/
│   ├── RobotViewer3D.vue          # 3D可视化主组件
│   ├── RobotScene.ts               # Three.js场景管理
│   └── RobotModel.ts               # 机器人模型加载与控制
├── composables/
│   └── useRobotVisualization.ts    # 3D可视化状态管理
├── assets/
│   └── models/
│       └── jaka-zu3.urdf           # JAKA Zu3 URDF模型
│       └── meshes/                 # STL/OBJ网格文件
└── utils/
    └── kinematics.ts               # 正向运动学计算（可选）
```

### 组件层级

```
RobotControlPanel.vue
├── RobotViewer3D.vue
│   ├── Canvas (Three.js renderer)
│   ├── Scene
│   │   ├── Camera
│   │   ├── Lights
│   │   ├── Robot Model (urdf-loader)
│   │   └── Grid Helper
│   └── Controls (OrbitControls)
└── RobotJointPanel.vue (现有)
```

---

## 📦 依赖包

### NPM包安装

```bash
npm install three @types/three
npm install urdf-loader
npm install @tweenjs/tween.js  # 平滑动画过渡
```

### 包大小评估
- three.js: ~580KB (gzipped)
- urdf-loader: ~20KB (gzipped)
- @tweenjs/tween.js: ~5KB (gzipped)

**总增量**: ~605KB (gzipped)

---

## 🔧 实施步骤

### Phase 1: 基础渲染（2-3天）

#### 任务1.1: 搭建Three.js场景
```typescript
// src/web/src/components/RobotScene.ts
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls'

export class RobotScene {
  scene: THREE.Scene
  camera: THREE.PerspectiveCamera
  renderer: THREE.WebGLRenderer
  controls: OrbitControls

  constructor(canvas: HTMLCanvasElement) {
    // 初始化场景
    this.scene = new THREE.Scene()
    this.scene.background = new THREE.Color(0x1a1a1a)

    // 相机
    this.camera = new THREE.PerspectiveCamera(
      50,
      canvas.width / canvas.height,
      0.1,
      1000
    )
    this.camera.position.set(2, 2, 2)

    // 渲染器
    this.renderer = new THREE.WebGLRenderer({ 
      canvas, 
      antialias: true 
    })
    this.renderer.setPixelRatio(window.devicePixelRatio)
    this.renderer.shadowMap.enabled = true

    // 轨道控制器
    this.controls = new OrbitControls(this.camera, canvas)
    this.controls.enableDamping = true

    // 灯光
    this.setupLights()

    // 网格
    this.addGrid()
  }

  setupLights() {
    // 环境光
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.6)
    this.scene.add(ambientLight)

    // 方向光（主光源）
    const dirLight = new THREE.DirectionalLight(0xffffff, 0.8)
    dirLight.position.set(5, 10, 7.5)
    dirLight.castShadow = true
    this.scene.add(dirLight)
  }

  addGrid() {
    const gridHelper = new THREE.GridHelper(10, 10)
    this.scene.add(gridHelper)
  }

  render() {
    this.controls.update()
    this.renderer.render(this.scene, this.camera)
  }

  dispose() {
    this.renderer.dispose()
    this.controls.dispose()
  }
}
```

#### 任务1.2: Vue组件封装
```vue
<!-- src/web/src/components/RobotViewer3D.vue -->
<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { RobotScene } from './RobotScene'

const canvasRef = ref<HTMLCanvasElement>()
let scene: RobotScene | null = null
let animationId: number | null = null

onMounted(() => {
  if (!canvasRef.value) return
  
  scene = new RobotScene(canvasRef.value)
  
  // 动画循环
  const animate = () => {
    animationId = requestAnimationFrame(animate)
    scene?.render()
  }
  animate()

  // 响应式尺寸
  const handleResize = () => {
    if (!canvasRef.value || !scene) return
    const { clientWidth, clientHeight } = canvasRef.value.parentElement!
    scene.camera.aspect = clientWidth / clientHeight
    scene.camera.updateProjectionMatrix()
    scene.renderer.setSize(clientWidth, clientHeight)
  }
  window.addEventListener('resize', handleResize)
  handleResize()
})

onUnmounted(() => {
  if (animationId) cancelAnimationFrame(animationId)
  scene?.dispose()
})
</script>

<template>
  <div class="robot-viewer-3d">
    <canvas ref="canvasRef"></canvas>
  </div>
</template>

<style scoped>
.robot-viewer-3d {
  width: 100%;
  height: 100%;
  position: relative;
}

canvas {
  width: 100%;
  height: 100%;
  display: block;
}
</style>
```

#### 验收标准
✅ 显示黑色背景的3D场景
✅ 鼠标可以旋转、缩放、平移视角
✅ 显示网格地面

---

### Phase 2: URDF模型加载（3-4天）

#### 前置准备：获取JAKA Zu3 URDF模型

**方案A：从JAKA官方获取**
1. 联系JAKA技术支持
2. 请求Zu3的URDF/SDF模型文件
3. 通常包含：
   - `jaka_zu3.urdf` - 模型描述文件
   - `meshes/` - STL/DAE网格文件

**方案B：从ROS包获取**
```bash
# 如果JAKA提供了ROS驱动包
git clone https://github.com/JAKArobotics/jaka_zu_driver
# 查找 urdf/ 或 description/ 目录
```

**方案C：手动创建简化URDF**（临时方案）
```xml
<?xml version="1.0"?>
<robot name="jaka_zu3">
  <!-- 基座 -->
  <link name="base_link">
    <visual>
      <geometry>
        <cylinder radius="0.1" length="0.2"/>
      </geometry>
      <material name="gray">
        <color rgba="0.5 0.5 0.5 1"/>
      </material>
    </visual>
  </link>

  <!-- 关节1 -->
  <joint name="joint1" type="revolute">
    <parent link="base_link"/>
    <child link="link1"/>
    <origin xyz="0 0 0.1" rpy="0 0 0"/>
    <axis xyz="0 0 1"/>
    <limit lower="-6.108" upper="6.108" effort="100" velocity="1.0"/>
  </joint>

  <link name="link1">
    <visual>
      <geometry>
        <cylinder radius="0.08" length="0.3"/>
      </geometry>
      <material name="blue">
        <color rgba="0.2 0.4 0.8 1"/>
      </material>
    </visual>
  </link>

  <!-- 重复定义joint2-6和link2-6... -->
</robot>
```

#### 任务2.1: 集成urdf-loader
```typescript
// src/web/src/components/RobotModel.ts
import * as THREE from 'three'
import URDFLoader from 'urdf-loader'

export class RobotModel {
  robot: THREE.Object3D | null = null
  joints: Map<string, THREE.Object3D> = new Map()

  async load(urdfPath: string): Promise<void> {
    const loader = new URDFLoader()
    
    // 设置网格路径
    loader.packages = {
      'jaka_zu3_description': '/models'
    }

    this.robot = await loader.loadAsync(urdfPath)
    
    // 收集所有关节
    this.robot.traverse((child) => {
      if (child.isURDFJoint) {
        this.joints.set(child.name, child)
      }
    })
  }

  setJointAngles(angles: number[]) {
    const jointNames = ['joint1', 'joint2', 'joint3', 'joint4', 'joint5', 'joint6']
    
    jointNames.forEach((name, index) => {
      const joint = this.joints.get(name)
      if (joint && angles[index] !== undefined) {
        joint.setJointValue(angles[index])
      }
    })
  }

  addToScene(scene: THREE.Scene) {
    if (this.robot) {
      scene.add(this.robot)
    }
  }

  dispose() {
    if (this.robot) {
      this.robot.traverse((child) => {
        if (child instanceof THREE.Mesh) {
          child.geometry.dispose()
          if (Array.isArray(child.material)) {
            child.material.forEach(m => m.dispose())
          } else {
            child.material.dispose()
          }
        }
      })
    }
  }
}
```

#### 任务2.2: 集成到场景
```typescript
// 更新 RobotScene.ts
export class RobotScene {
  // ... 现有代码
  robotModel: RobotModel

  async init(urdfPath: string) {
    this.robotModel = new RobotModel()
    await this.robotModel.load(urdfPath)
    this.robotModel.addToScene(this.scene)
  }

  updateJointAngles(angles: number[]) {
    this.robotModel.setJointAngles(angles)
  }
}
```

#### 验收标准
✅ 成功加载URDF模型
✅ 模型显示在3D场景中
✅ 可以手动设置关节角度并看到模型运动

---

### Phase 3: 实时数据同步（2-3天）

#### 任务3.1: 创建可视化状态管理
```typescript
// src/web/src/composables/useRobotVisualization.ts
import { ref, watch } from 'vue'
import { useJakaControl } from './useJakaControl'
import TWEEN from '@tweenjs/tween.js'

export function useRobotVisualization() {
  const { robot } = useJakaControl()
  const currentJointAngles = ref<number[]>([0, 0, 0, 0, 0, 0])
  const isAnimating = ref(false)

  // 平滑过渡到新的关节角度
  function animateToAngles(targetAngles: number[], duration = 500) {
    if (isAnimating.value) return

    isAnimating.value = true
    const startAngles = [...currentJointAngles.value]

    new TWEEN.Tween(startAngles)
      .to(targetAngles, duration)
      .easing(TWEEN.Easing.Quadratic.Out)
      .onUpdate(() => {
        currentJointAngles.value = [...startAngles]
      })
      .onComplete(() => {
        isAnimating.value = false
      })
      .start()
  }

  // 监听机器人状态变化
  watch(
    () => robot.value?.jointPositionsRad,
    (newAngles) => {
      if (newAngles) {
        animateToAngles([...newAngles])
      }
    },
    { immediate: true }
  )

  return {
    currentJointAngles,
    isAnimating,
  }
}
```

#### 任务3.2: 连接可视化和数据
```vue
<!-- 更新 RobotViewer3D.vue -->
<script setup lang="ts">
import { watch } from 'vue'
import { useRobotVisualization } from '../composables/useRobotVisualization'

// ... 现有代码

const { currentJointAngles } = useRobotVisualization()

watch(currentJointAngles, (angles) => {
  scene?.updateJointAngles(angles)
}, { deep: true })

// Tween.js动画循环
const animate = () => {
  animationId = requestAnimationFrame(animate)
  TWEEN.update()  // 更新动画
  scene?.render()
}
</script>
```

#### 验收标准
✅ 3D模型实时跟随后端返回的关节角度
✅ 角度变化有平滑过渡动画
✅ 延迟<100ms

---

### Phase 4: 交互增强（2天）

#### 任务4.1: 关节角度标注
```typescript
// src/web/src/utils/labelRenderer.ts
import * as THREE from 'three'

export function createJointLabel(
  jointName: string,
  angle: number,
  position: THREE.Vector3
): THREE.Sprite {
  const canvas = document.createElement('canvas')
  const context = canvas.getContext('2d')!
  canvas.width = 256
  canvas.height = 64

  // 绘制标签背景
  context.fillStyle = 'rgba(0, 0, 0, 0.7)'
  context.fillRect(0, 0, canvas.width, canvas.height)

  // 绘制文字
  context.font = '24px monospace'
  context.fillStyle = '#00ff00'
  context.textAlign = 'center'
  context.fillText(
    `${jointName}: ${angle.toFixed(2)}°`,
    canvas.width / 2,
    canvas.height / 2
  )

  const texture = new THREE.CanvasTexture(canvas)
  const material = new THREE.SpriteMaterial({ map: texture })
  const sprite = new THREE.Sprite(material)
  sprite.position.copy(position)
  sprite.scale.set(0.5, 0.125, 1)

  return sprite
}
```

#### 任务4.2: 运动高亮
```typescript
// 高亮运动中的关节
export class RobotModel {
  highlightJoint(jointName: string) {
    const joint = this.joints.get(jointName)
    if (joint) {
      // 添加发光效果
      joint.traverse((child) => {
        if (child instanceof THREE.Mesh) {
          child.material.emissive = new THREE.Color(0x00ff00)
          child.material.emissiveIntensity = 0.5
        }
      })
    }
  }

  clearHighlight() {
    this.robot?.traverse((child) => {
      if (child instanceof THREE.Mesh) {
        child.material.emissive = new THREE.Color(0x000000)
      }
    })
  }
}
```

#### 任务4.3: 控制面板集成
```vue
<!-- 在RobotControlPanel中添加可视化切换 -->
<template>
  <div class="robot-control-layout">
    <div class="control-section">
      <!-- 现有控制面板 -->
    </div>
    
    <div v-if="show3DViewer" class="viewer-section">
      <RobotViewer3D />
    </div>
    
    <button @click="show3DViewer = !show3DViewer">
      {{ show3DViewer ? '隐藏' : '显示' }} 3D可视化
    </button>
  </div>
</template>

<style scoped>
.robot-control-layout {
  display: grid;
  grid-template-columns: 400px 1fr;
  gap: 1rem;
  height: 100vh;
}

.viewer-section {
  min-height: 600px;
}
</style>
```

#### 验收标准
✅ 显示实时关节角度标注
✅ 运动中的关节高亮显示
✅ 可以切换3D视图显示/隐藏

---

### Phase 5: 性能优化（1-2天）

#### 优化点
1. **LOD (Level of Detail)**：远距离使用简化模型
2. **实例化渲染**：如果有多个相同组件
3. **纹理压缩**：使用压缩纹理格式
4. **按需渲染**：静止时停止渲染循环

```typescript
export class RobotScene {
  needsUpdate = true

  render() {
    if (!this.needsUpdate) return
    
    this.controls.update()
    this.renderer.render(this.scene, this.camera)
    
    // 如果没有动画在运行，停止渲染
    if (!this.isAnimating()) {
      this.needsUpdate = false
    }
  }

  markDirty() {
    this.needsUpdate = true
  }
}
```

---

## 📊 资源清单

### 必需资源
- [ ] JAKA Zu3 URDF模型文件
- [ ] JAKA Zu3 STL/OBJ网格文件
- [ ] JAKA Zu3 DH参数（用于验证）

### 参考资料
1. **Three.js官方文档**：https://threejs.org/docs/
2. **urdf-loader**：https://github.com/gkjohnson/urdf-loaders
3. **ROS URDF教程**：http://wiki.ros.org/urdf/Tutorials
4. **WebGL最佳实践**：https://webglfundamentals.org/

### 示例项目
1. **ROS3D.js**：https://github.com/RobotWebTools/ros3djs
2. **robot-web-tools**：http://robotwebtools.org/
3. **Three.js机器人示例**：https://threejs.org/examples/?q=robot

---

## ⏱️ 时间估算

| 阶段 | 任务 | 工作量 | 依赖 |
|-----|------|--------|------|
| Phase 1 | 基础渲染 | 2-3天 | - |
| Phase 2 | URDF加载 | 3-4天 | URDF模型文件 |
| Phase 3 | 数据同步 | 2-3天 | Phase 1, 2 |
| Phase 4 | 交互增强 | 2天 | Phase 3 |
| Phase 5 | 性能优化 | 1-2天 | Phase 4 |

**总计**: 10-14天（2-3周）

---

## 🚧 风险与应对

### 风险1：无法获取URDF模型
**影响**: 高
**应对**:
- 方案A：手动创建简化URDF（基于产品手册）
- 方案B：使用基础几何体代替（圆柱、立方体）
- 方案C：联系JAKA技术支持获取CAD模型并转换

### 风险2：性能问题
**影响**: 中
**应对**:
- 简化模型精度
- 使用LOD技术
- 按需渲染

### 风险3：浏览器兼容性
**影响**: 低
**应对**:
- 检测WebGL支持
- 提供降级方案（2D示意图）

---

## 📈 后续扩展

### 未来功能
1. **轨迹预览**：显示计划运动路径
2. **碰撞检测**：可视化碰撞区域
3. **工作空间边界**：显示可达工作空间
4. **多视角切换**：顶视图、侧视图、正视图
5. **录制回放**：记录和回放运动序列
6. **VR/AR支持**：使用WebXR API

---

## ✅ 验收标准

### Phase 1
- [ ] 3D场景正常渲染
- [ ] 鼠标交互正常（旋转、缩放、平移）
- [ ] 帧率稳定在60fps

### Phase 2
- [ ] URDF模型成功加载
- [ ] 6个关节可独立控制
- [ ] 关节角度范围正确

### Phase 3
- [ ] 后端数据实时同步到3D模型
- [ ] 延迟<100ms
- [ ] 平滑过渡动画

### Phase 4
- [ ] 关节角度标注清晰可读
- [ ] 运动关节高亮显示
- [ ] UI集成无缝

### Phase 5
- [ ] 静态场景渲染停止（CPU<5%）
- [ ] 内存占用稳定（<200MB）
- [ ] 打包体积增量<1MB

---

## 🎬 开始执行前检查清单

### 必须完成
- [ ] 确认Three.js版本（建议r162+）
- [ ] 获取或创建JAKA Zu3 URDF模型
- [ ] 验证DH参数准确性
- [ ] 测试现有项目构建流程

### 建议完成
- [ ] 搭建Three.js playground快速验证
- [ ] 调研现有JAKA用户的3D可视化方案
- [ ] 准备降级方案（2D示意图）

---

## 📞 后续支持

实施过程中如遇到问题：
1. **URDF模型问题**：查阅ROS URDF文档
2. **Three.js渲染问题**：参考官方示例和Stack Overflow
3. **性能问题**：使用Chrome DevTools性能分析器

**准备就绪后即可开始Phase 1！**
