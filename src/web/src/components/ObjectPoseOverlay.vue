<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import type { ObjectPose, ObjectPoseImagePoint, ObjectPoseTracking } from '../api/camera'

const props = defineProps<{
  objectPose: ObjectPoseTracking | null
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

type ScreenPoint = {
  x: number
  y: number
}

/** Draws only normalized geometry that belongs to the exact displayed camera pixels. */
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
  for (const objectPose of props.objectPose?.objects ?? []) {
    drawObjectPose(context, objectPose, imageBounds)
  }
  context.restore()
}

function drawObjectPose(
  context: CanvasRenderingContext2D,
  objectPose: ObjectPose,
  imageBounds: ImageContentBounds,
): void {
  const hasWarning = objectPose.warning !== null && objectPose.warning.trim().length > 0
  const color = hasWarning ? '#fbbf24' : '#34d399'
  const box = objectPose.boundingBox
  const left = imageBounds.left + box.x * imageBounds.width
  const top = imageBounds.top + box.y * imageBounds.height
  const boxWidth = box.width * imageBounds.width
  const boxHeight = box.height * imageBounds.height

  context.lineWidth = hasWarning ? 2.5 : 2
  context.strokeStyle = color
  context.setLineDash(hasWarning ? [5, 3] : [])
  context.strokeRect(left, top, boxWidth, boxHeight)
  context.setLineDash([])

  const contour = objectPose.contour.map((point) => toScreenPoint(point, imageBounds))
  if (contour.length >= 2) {
    context.beginPath()
    context.moveTo(contour[0].x, contour[0].y)
    for (const point of contour.slice(1)) {
      context.lineTo(point.x, point.y)
    }
    if (contour.length >= 3) {
      context.closePath()
    }
    context.lineWidth = 1.5
    context.strokeStyle = color
    context.stroke()
  }

  const center = toScreenPoint(objectPose.normalizedCenter, imageBounds)
  drawPrincipalAxis(context, contour, center, boxWidth, boxHeight, color)
  drawCenterMarker(context, center, color)
  drawLabel(context, objectPose, center, imageBounds, color)
}

/** Draws an undirected image-space main axis, never guessing base-frame yaw on the preview. */
function drawPrincipalAxis(
  context: CanvasRenderingContext2D,
  contour: ScreenPoint[],
  center: ScreenPoint,
  boxWidth: number,
  boxHeight: number,
  color: string,
): void {
  const angle = principalAxisAngle(contour, center)
  if (angle === null) {
    return
  }

  const axisLength = Math.max(18, Math.min(52, Math.hypot(boxWidth, boxHeight) * 0.35))
  const offsetX = Math.cos(angle) * axisLength
  const offsetY = Math.sin(angle) * axisLength
  context.beginPath()
  context.moveTo(center.x - offsetX, center.y - offsetY)
  context.lineTo(center.x + offsetX, center.y + offsetY)
  context.lineWidth = 2
  context.strokeStyle = color
  context.stroke()

  for (const point of [
    { x: center.x - offsetX, y: center.y - offsetY },
    { x: center.x + offsetX, y: center.y + offsetY },
  ]) {
    context.beginPath()
    context.fillStyle = color
    context.arc(point.x, point.y, 2, 0, Math.PI * 2)
    context.fill()
  }
}

function principalAxisAngle(contour: ScreenPoint[], center: ScreenPoint): number | null {
  if (contour.length < 2) {
    return null
  }

  let covarianceX = 0
  let covarianceY = 0
  let covarianceXY = 0
  for (const point of contour) {
    const deltaX = point.x - center.x
    const deltaY = point.y - center.y
    covarianceX += deltaX * deltaX
    covarianceY += deltaY * deltaY
    covarianceXY += deltaX * deltaY
  }
  if (covarianceX + covarianceY < 1) {
    return null
  }
  return 0.5 * Math.atan2(2 * covarianceXY, covarianceX - covarianceY)
}

function drawCenterMarker(context: CanvasRenderingContext2D, center: ScreenPoint, color: string): void {
  context.beginPath()
  context.fillStyle = '#020617'
  context.strokeStyle = color
  context.lineWidth = 2
  context.arc(center.x, center.y, 5, 0, Math.PI * 2)
  context.fill()
  context.stroke()

  context.beginPath()
  context.moveTo(center.x - 8, center.y)
  context.lineTo(center.x + 8, center.y)
  context.moveTo(center.x, center.y - 8)
  context.lineTo(center.x, center.y + 8)
  context.stroke()
}

function drawLabel(
  context: CanvasRenderingContext2D,
  objectPose: ObjectPose,
  center: ScreenPoint,
  imageBounds: ImageContentBounds,
  color: string,
): void {
  const confidence = Math.round(objectPose.confidence * 100)
  const label = `${objectPose.profileId} ${confidence}%`
  context.font = '600 11px sans-serif'
  const textWidth = context.measureText(label).width
  const labelLeft = Math.min(
    Math.max(imageBounds.left + 3, center.x + 10),
    imageBounds.left + imageBounds.width - textWidth - 9,
  )
  const labelTop = Math.max(imageBounds.top + 3, center.y - 22)
  context.fillStyle = 'rgba(2, 6, 23, 0.86)'
  context.fillRect(labelLeft - 4, labelTop - 10, textWidth + 8, 16)
  context.fillStyle = color
  context.fillText(label, labelLeft, labelTop + 2)
}

function toScreenPoint(point: ObjectPoseImagePoint, imageBounds: ImageContentBounds): ScreenPoint {
  return {
    x: imageBounds.left + point.x * imageBounds.width,
    y: imageBounds.top + point.y * imageBounds.height,
  }
}

/** Resolves the actual source-pixel rectangle inside an object-contain image element. */
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

/** Observes object-contain sizing changes without touching the continuous MJPEG source. */
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

watch(() => props.objectPose, () => nextTick(draw), { deep: true })
watch(() => props.imageElement, (image) => {
  observeImage(image)
  nextTick(draw)
})
</script>

<template>
  <canvas ref="canvas" class="pointer-events-none absolute inset-0 h-full w-full" aria-hidden="true" />
</template>
