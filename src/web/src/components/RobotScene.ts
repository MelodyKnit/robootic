/**
 * Three.js 3D scene for robot visualization
 *
 * Manages the WebGL rendering context, camera, lights, and controls
 * for real-time robot arm visualization.
 */

import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { RobotModel, type JointAngles } from './RobotModel'

export class RobotScene {
  scene: THREE.Scene
  camera: THREE.PerspectiveCamera
  renderer: THREE.WebGLRenderer
  controls: OrbitControls
  needsUpdate: boolean = true
  robotModel: RobotModel | null = null
  private animationId: number | null = null

  constructor(canvas: HTMLCanvasElement) {
    // Initialize scene
    this.scene = new THREE.Scene()
    this.scene.background = new THREE.Color(0x0f172a) // slate-900

    // Setup camera
    this.camera = new THREE.PerspectiveCamera(
      50, // FOV
      canvas.clientWidth / canvas.clientHeight, // aspect
      0.1, // near
      1000 // far
    )
    this.camera.position.set(2, 2, 2)
    this.camera.lookAt(0, 0, 0)

    // Setup renderer
    this.renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: true,
      alpha: false,
    })
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    this.renderer.setSize(canvas.clientWidth, canvas.clientHeight)
    this.renderer.shadowMap.enabled = true
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap

    // Setup orbit controls
    this.controls = new OrbitControls(this.camera, canvas)
    this.controls.enableDamping = true
    this.controls.dampingFactor = 0.05
    this.controls.minDistance = 0.5
    this.controls.maxDistance = 10
    this.controls.addEventListener('change', () => {
      this.needsUpdate = true
    })

    // Setup lights
    this.setupLights()

    // Add ground grid
    this.addGrid()

    // Add axes helper (for debugging)
    const axesHelper = new THREE.AxesHelper(1)
    this.scene.add(axesHelper)
  }

  private setupLights(): void {
    // Ambient light (soft overall illumination)
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.6)
    this.scene.add(ambientLight)

    // Main directional light (sun-like)
    const dirLight = new THREE.DirectionalLight(0xffffff, 0.8)
    dirLight.position.set(5, 10, 7.5)
    dirLight.castShadow = true
    dirLight.shadow.mapSize.width = 2048
    dirLight.shadow.mapSize.height = 2048
    dirLight.shadow.camera.near = 0.5
    dirLight.shadow.camera.far = 50
    dirLight.shadow.camera.left = -5
    dirLight.shadow.camera.right = 5
    dirLight.shadow.camera.top = 5
    dirLight.shadow.camera.bottom = -5
    this.scene.add(dirLight)

    // Fill light (soften shadows)
    const fillLight = new THREE.DirectionalLight(0x94a3b8, 0.3) // slate-400
    fillLight.position.set(-5, 3, -5)
    this.scene.add(fillLight)
  }

  private addGrid(): void {
    const gridHelper = new THREE.GridHelper(
      10, // size
      20, // divisions
      0x475569, // center line color (slate-600)
      0x1e293b  // grid color (slate-800)
    )
    gridHelper.position.y = -0.001 // slightly below ground to avoid z-fighting
    this.scene.add(gridHelper)
  }

  /**
   * Start the render loop
   */
  startRenderLoop(): void {
    const animate = () => {
      this.animationId = requestAnimationFrame(animate)

      // Only render if something changed
      if (this.needsUpdate) {
        this.controls.update()
        this.renderer.render(this.scene, this.camera)

        // Don't set to false if controls are damping
        if (!this.controls.enabled || Math.abs(this.controls.getAzimuthalAngle()) < 0.001) {
          this.needsUpdate = false
        }
      }
    }
    animate()
  }

  /**
   * Stop the render loop
   */
  stopRenderLoop(): void {
    if (this.animationId !== null) {
      cancelAnimationFrame(this.animationId)
      this.animationId = null
    }
  }

  /**
   * Mark scene as needing re-render
   */
  markDirty(): void {
    this.needsUpdate = true
  }

  /**
   * Handle canvas resize
   */
  handleResize(width: number, height: number): void {
    this.camera.aspect = width / height
    this.camera.updateProjectionMatrix()
    this.renderer.setSize(width, height)
    this.needsUpdate = true
  }

  /**
   * 加载机器人URDF模型
   */
  async loadRobotModel(urdfPath: string): Promise<void> {
    if (this.robotModel) {
      console.warn('[RobotScene] Disposing existing robot model')
      this.scene.remove(this.robotModel.getObject()!)
      this.robotModel.dispose()
    }

    this.robotModel = new RobotModel()
    await this.robotModel.load(urdfPath)

    const robotObject = this.robotModel.getObject()
    if (robotObject) {
      this.scene.add(robotObject)
      this.needsUpdate = true
      console.log('[RobotScene] Robot model added to scene')
    }
  }

  /**
   * 更新机器人关节角度
   */
  updateRobotAngles(angles: JointAngles): void {
    if (!this.robotModel) {
      console.warn('[RobotScene] Cannot update angles: robot model not loaded')
      return
    }

    this.robotModel.setJointAngles(angles)
    this.needsUpdate = true
  }

  /**
   * 获取当前机器人关节角度
   */
  getRobotAngles(): JointAngles | null {
    return this.robotModel?.getJointAngles() ?? null
  }

  /**
   * 检查机器人模型是否已加载
   */
  isRobotLoaded(): boolean {
    return this.robotModel?.isLoaded() ?? false
  }

  /**
   * Cleanup resources
   */
  dispose(): void {
    this.stopRenderLoop()
    this.controls.dispose()
    this.renderer.dispose()

    // Dispose robot model
    if (this.robotModel) {
      this.robotModel.dispose()
      this.robotModel = null
    }

    // Dispose all geometries and materials
    this.scene.traverse((object) => {
      if (object instanceof THREE.Mesh) {
        object.geometry.dispose()
        if (Array.isArray(object.material)) {
          object.material.forEach(material => material.dispose())
        } else {
          object.material.dispose()
        }
      }
    })
  }
}
