import * as THREE from 'three'
import URDFLoader, { URDFRobot } from 'urdf-loader'

export interface JointAngles {
  joint1: number
  joint2: number
  joint3: number
  joint4: number
  joint5: number
  joint6: number
}

/**
 * 管理JAKA机器人的URDF模型加载和关节控制
 */
export class RobotModel {
  private robot: URDFRobot | null = null
  private loader: URDFLoader
  private loading = false
  private loadError: Error | null = null

  constructor() {
    this.loader = new URDFLoader()
    // URDF loader会自动处理材质，不需要自定义mesh加载回调
  }

  /**
   * 从指定路径加载URDF模型
   */
  async load(urdfPath: string): Promise<void> {
    if (this.loading) {
      throw new Error('Model is already loading')
    }

    this.loading = true
    this.loadError = null

    try {
      const robot = await new Promise<URDFRobot>((resolve, reject) => {
        this.loader.load(
          urdfPath,
          (result) => resolve(result),
          undefined,
          (error) => reject(error)
        )
      })

      this.robot = robot

      // 设置模型接收和投射阴影
      this.robot.traverse((child) => {
        if (child instanceof THREE.Mesh) {
          child.castShadow = true
          child.receiveShadow = true
        }
      })

      console.log('[RobotModel] URDF loaded successfully:', {
        joints: Object.keys(this.robot.joints),
        links: Object.keys(this.robot.links),
      })
    } catch (error) {
      this.loadError = error as Error
      console.error('[RobotModel] Failed to load URDF:', error)
      throw error
    } finally {
      this.loading = false
    }
  }

  /**
   * 获取加载的机器人对象，可添加到Three.js场景中
   */
  getObject(): THREE.Object3D | null {
    return this.robot
  }

  /**
   * 更新所有关节角度（单位：弧度）
   */
  setJointAngles(angles: JointAngles): void {
    if (!this.robot) {
      console.warn('[RobotModel] Cannot set joint angles: robot not loaded')
      return
    }

    const jointNames = ['joint1', 'joint2', 'joint3', 'joint4', 'joint5', 'joint6']

    jointNames.forEach((name) => {
      const joint = this.robot!.joints[name]
      if (joint) {
        const angle = angles[name as keyof JointAngles]
        if (typeof angle === 'number' && !isNaN(angle)) {
          joint.setJointValue(angle)
        }
      } else {
        console.warn(`[RobotModel] Joint not found: ${name}`)
      }
    })
  }

  /**
   * 获取当前所有关节角度
   */
  getJointAngles(): JointAngles | null {
    if (!this.robot) {
      return null
    }

    return {
      joint1: this.robot.joints.joint1?.angle ?? 0,
      joint2: this.robot.joints.joint2?.angle ?? 0,
      joint3: this.robot.joints.joint3?.angle ?? 0,
      joint4: this.robot.joints.joint4?.angle ?? 0,
      joint5: this.robot.joints.joint5?.angle ?? 0,
      joint6: this.robot.joints.joint6?.angle ?? 0,
    }
  }

  /**
   * 平滑过渡到目标关节角度
   */
  animateToAngles(targetAngles: JointAngles, duration = 1000): Promise<void> {
    if (!this.robot) {
      return Promise.reject(new Error('Robot not loaded'))
    }

    const startAngles = this.getJointAngles()!
    const startTime = Date.now()

    return new Promise((resolve) => {
      const animate = () => {
        const elapsed = Date.now() - startTime
        const progress = Math.min(elapsed / duration, 1)

        // 使用缓动函数（ease-in-out）
        const eased = progress < 0.5
          ? 2 * progress * progress
          : 1 - Math.pow(-2 * progress + 2, 2) / 2

        const currentAngles: JointAngles = {
          joint1: startAngles.joint1 + (targetAngles.joint1 - startAngles.joint1) * eased,
          joint2: startAngles.joint2 + (targetAngles.joint2 - startAngles.joint2) * eased,
          joint3: startAngles.joint3 + (targetAngles.joint3 - startAngles.joint3) * eased,
          joint4: startAngles.joint4 + (targetAngles.joint4 - startAngles.joint4) * eased,
          joint5: startAngles.joint5 + (targetAngles.joint5 - startAngles.joint5) * eased,
          joint6: startAngles.joint6 + (targetAngles.joint6 - startAngles.joint6) * eased,
        }

        this.setJointAngles(currentAngles)

        if (progress < 1) {
          requestAnimationFrame(animate)
        } else {
          resolve()
        }
      }

      animate()
    })
  }

  /**
   * 重置所有关节到零位
   */
  resetPose(): void {
    this.setJointAngles({
      joint1: 0,
      joint2: 0,
      joint3: 0,
      joint4: 0,
      joint5: 0,
      joint6: 0,
    })
  }

  /**
   * 检查模型是否已加载
   */
  isLoaded(): boolean {
    return this.robot !== null
  }

  /**
   * 检查是否正在加载
   */
  isLoading(): boolean {
    return this.loading
  }

  /**
   * 获取加载错误
   */
  getLoadError(): Error | null {
    return this.loadError
  }

  /**
   * 清理资源
   */
  dispose(): void {
    if (this.robot) {
      this.robot.traverse((child) => {
        if (child instanceof THREE.Mesh) {
          child.geometry.dispose()
          if (Array.isArray(child.material)) {
            child.material.forEach((m) => m.dispose())
          } else {
            child.material.dispose()
          }
        }
      })
      this.robot = null
    }
  }
}
