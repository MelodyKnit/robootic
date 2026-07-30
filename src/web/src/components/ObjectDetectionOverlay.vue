<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import type { DetectionBoundingBox, DetectionTracking, ObjectDetection } from '../api/camera'

const props = defineProps<{
  detection: DetectionTracking | null
  imageElement: HTMLImageElement | null
}>()

const canvas = ref<HTMLCanvasElement | null>(null)

let resizeObserver: ResizeObserver | undefined
let observedImage: HTMLImageElement | null = null

type ImageContentBounds = {
  left: number
  top: number
  width: number
  height: number
}

const boxColors = ['#38bdf8', '#34d399', '#fbbf24', '#fb7185', '#a78bfa', '#22d3ee']

/** Draws only normalized detections belonging to the currently displayed camera pixels. */
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

  context.save()
  context.beginPath()
  context.rect(imageBounds.left, imageBounds.top, imageBounds.width, imageBounds.height)
  context.clip()
  for (const candidate of props.detection?.detections ?? []) {
    drawDetection(context, candidate, imageBounds)
  }
  context.restore()
}

function drawDetection(
  context: CanvasRenderingContext2D,
  detection: ObjectDetection,
  imageBounds: ImageContentBounds,
): void {
  const color = colorForLabel(detection.label)
  const box = toScreenBox(detection.boundingBox, imageBounds)

  context.strokeStyle = color
  context.lineWidth = 2
  context.strokeRect(box.left, box.top, box.width, box.height)

  const confidence = Math.round(detection.confidence * 100)
  const label = `${detection.label} ${confidence}%`
  context.font = '600 11px sans-serif'
  const labelWidth = Math.min(context.measureText(label).width + 10, imageBounds.width)
  const labelHeight = 19
  const labelLeft = Math.min(
    Math.max(imageBounds.left, box.left),
    imageBounds.left + imageBounds.width - labelWidth,
  )
  const labelTop = Math.max(imageBounds.top, box.top - labelHeight)

  context.fillStyle = 'rgba(2, 6, 23, 0.9)'
  context.fillRect(labelLeft, labelTop, labelWidth, labelHeight)
  context.fillStyle = color
  context.fillText(label, labelLeft + 5, labelTop + 13)
}

function toScreenBox(box: DetectionBoundingBox, imageBounds: ImageContentBounds) {
  return {
    left: imageBounds.left + box.x * imageBounds.width,
    top: imageBounds.top + box.y * imageBounds.height,
    width: box.width * imageBounds.width,
    height: box.height * imageBounds.height,
  }
}

function colorForLabel(label: string): string {
  let hash = 0
  for (let index = 0; index < label.length; index += 1) {
    hash = ((hash << 5) - hash + label.charCodeAt(index)) | 0
  }
  return boxColors[Math.abs(hash) % boxColors.length]
}

/** Resolves the actual source-image rectangle inside an object-contain element. */
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

/** Observes layout changes without altering the continuous MJPEG element. */
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
  void nextTick(draw)
})

onBeforeUnmount(() => {
  if (observedImage !== null) {
    observedImage.removeEventListener('load', draw)
  }
  resizeObserver?.disconnect()
})

watch(() => props.detection, () => void nextTick(draw), { deep: true })
watch(() => props.imageElement, (image) => {
  observeImage(image)
  void nextTick(draw)
})
</script>

<template>
  <canvas ref="canvas" class="pointer-events-none absolute inset-0 h-full w-full" aria-hidden="true" />
</template>

