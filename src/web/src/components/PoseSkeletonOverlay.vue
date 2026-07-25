<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import type { PoseTracking, VisionAnalysis } from '../api/camera'

const props = defineProps<{
  pose: PoseTracking | null
  analysis: VisionAnalysis | null
  imageElement: HTMLImageElement | null
}>()

const canvas = ref<HTMLCanvasElement | null>(null)

const skeletonEdges: Array<[string, string]> = [
  ['left_ankle', 'left_knee'], ['left_knee', 'left_hip'],
  ['right_ankle', 'right_knee'], ['right_knee', 'right_hip'],
  ['left_hip', 'right_hip'], ['left_shoulder', 'left_hip'],
  ['right_shoulder', 'right_hip'], ['left_shoulder', 'right_shoulder'],
  ['left_shoulder', 'left_elbow'], ['left_elbow', 'left_wrist'],
  ['right_shoulder', 'right_elbow'], ['right_elbow', 'right_wrist'],
  ['left_eye', 'right_eye'], ['nose', 'left_eye'], ['nose', 'right_eye'],
  ['left_eye', 'left_ear'], ['right_eye', 'right_ear'],
  ['left_ear', 'left_shoulder'], ['right_ear', 'right_shoulder'],
]

let resizeObserver: ResizeObserver | undefined
let observedImage: HTMLImageElement | null = null

type ImageContentBounds = {
  left: number
  top: number
  width: number
  height: number
}

/** Redraws normalized pose coordinates at the exact CSS size of the image wrapper. */
function draw(): void {
  const element = canvas.value
  if (element === null) {
    return
  }

  const bounds = element.getBoundingClientRect()
  const width = Math.max(1, bounds.width)
  const height = Math.max(1, bounds.height)
  const pixelRatio = window.devicePixelRatio || 1
  const bitmapWidth = Math.round(width * pixelRatio)
  const bitmapHeight = Math.round(height * pixelRatio)
  if (element.width !== bitmapWidth || element.height !== bitmapHeight) {
    element.width = bitmapWidth
    element.height = bitmapHeight
  }

  const context = element.getContext('2d')
  if (context === null) {
    return
  }
  context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0)
  context.clearRect(0, 0, width, height)

  const imageBounds = resolveImageContentBounds(element, props.imageElement)
  if (imageBounds === null) {
    return
  }

  for (const person of props.analysis?.persons ?? []) {
    const box = person.boundingBox
    context.lineWidth = person.selected ? 2.5 : 1.5
    context.strokeStyle = person.selected ? 'rgba(245, 158, 11, 0.95)' : 'rgba(56, 189, 248, 0.72)'
    context.strokeRect(
      imageBounds.left + box.x * imageBounds.width,
      imageBounds.top + box.y * imageBounds.height,
      box.width * imageBounds.width,
      box.height * imageBounds.height,
    )
  }

  const person = props.pose?.person
  if (person === null || person === undefined || !props.pose?.drawSkeleton) {
    return
  }

  const joints = new Map(person.joints.map((joint) => [joint.name, joint]))
  context.lineWidth = 2
  context.strokeStyle = 'rgba(56, 189, 248, 0.9)'
  for (const [startName, endName] of skeletonEdges) {
    const start = joints.get(startName)
    const end = joints.get(endName)
    if (start === undefined || end === undefined || start.confidence < 0.1 || end.confidence < 0.1) {
      continue
    }
    context.beginPath()
    context.moveTo(
      imageBounds.left + start.normalizedX * imageBounds.width,
      imageBounds.top + start.normalizedY * imageBounds.height,
    )
    context.lineTo(
      imageBounds.left + end.normalizedX * imageBounds.width,
      imageBounds.top + end.normalizedY * imageBounds.height,
    )
    context.stroke()
  }

  for (const joint of person.joints) {
    if (joint.confidence < 0.1) {
      continue
    }
    const isTarget = props.pose?.valid === true && joint.name === props.pose.targetJoint
    context.beginPath()
    context.fillStyle = isTarget ? '#f59e0b' : '#e0f2fe'
    context.arc(
      imageBounds.left + joint.normalizedX * imageBounds.width,
      imageBounds.top + joint.normalizedY * imageBounds.height,
      isTarget ? 5 : 3,
      0,
      Math.PI * 2,
    )
    context.fill()
  }
}

/** Resolve the source-pixel rectangle inside an object-contain image element. */
function resolveImageContentBounds(
  canvasElement: HTMLCanvasElement,
  image: HTMLImageElement | null,
): ImageContentBounds | null {
  if (image === null || image.naturalWidth <= 0 || image.naturalHeight <= 0) {
    return null
  }
  const canvasBounds = canvasElement.getBoundingClientRect()
  const imageBounds = image.getBoundingClientRect()
  if (imageBounds.width <= 0 || imageBounds.height <= 0) {
    return null
  }
  const scale = Math.min(
    imageBounds.width / image.naturalWidth,
    imageBounds.height / image.naturalHeight,
  )
  const contentWidth = image.naturalWidth * scale
  const contentHeight = image.naturalHeight * scale
  return {
    left: imageBounds.left - canvasBounds.left + (imageBounds.width - contentWidth) / 2,
    top: imageBounds.top - canvasBounds.top + (imageBounds.height - contentHeight) / 2,
    width: contentWidth,
    height: contentHeight,
  }
}

/** Observe the image box and load state because object-contain can change its content rectangle. */
function observeImage(image: HTMLImageElement | null): void {
  if (observedImage === image) {
    return
  }
  if (observedImage !== null) {
    resizeObserver?.unobserve(observedImage)
    observedImage.removeEventListener('load', draw)
  }
  observedImage = image
  if (image !== null) {
    resizeObserver?.observe(image)
    image.addEventListener('load', draw)
  }
}

onMounted(() => {
  if (canvas.value !== null) {
    resizeObserver = new ResizeObserver(draw)
    resizeObserver.observe(canvas.value)
  }
  observeImage(props.imageElement)
  nextTick(draw)
})

onBeforeUnmount(() => {
  if (observedImage !== null) {
    observedImage.removeEventListener('load', draw)
  }
  resizeObserver?.disconnect()
})
watch(() => props.pose, () => nextTick(draw), { deep: true })
watch(() => props.analysis, () => nextTick(draw), { deep: true })
watch(() => props.imageElement, (image) => {
  observeImage(image)
  nextTick(draw)
})
</script>

<template>
  <canvas ref="canvas" class="pointer-events-none absolute inset-0 h-full w-full" aria-hidden="true" />
</template>
