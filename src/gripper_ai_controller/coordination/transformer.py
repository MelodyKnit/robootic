"""坐标转换类 - 实现像素到机器人坐标系的转换链"""
import numpy as np
from typing import Tuple, Optional, Dict, Any
from dataclasses import dataclass


@dataclass
class Point3D:
    """三维点坐标"""
    x: float
    y: float
    z: float

    def to_array(self) -> np.ndarray:
        return np.array([self.x, self.y, self.z])

    @classmethod
    def from_array(cls, arr: np.ndarray) -> "Point3D":
        return cls(x=float(arr[0]), y=float(arr[1]), z=float(arr[2]))


@dataclass
class Transform3D:
    """3D刚体变换 (旋转 + 平移)"""
    rotation: np.ndarray  # 3x3 旋转矩阵
    translation: np.ndarray  # 3x1 平移向量

    def apply(self, point: Point3D) -> Point3D:
        """应用变换到点"""
        p = point.to_array().reshape(3, 1)
        transformed = self.rotation @ p + self.translation.reshape(3, 1)
        return Point3D.from_array(transformed.flatten())

    def to_matrix(self) -> np.ndarray:
        """转换为4x4齐次变换矩阵"""
        T = np.eye(4)
        T[:3, :3] = self.rotation
        T[:3, 3] = self.translation
        return T

    @classmethod
    def from_matrix(cls, matrix: np.ndarray) -> "Transform3D":
        """从4x4齐次矩阵创建"""
        return cls(
            rotation=matrix[:3, :3].copy(),
            translation=matrix[:3, 3].copy()
        )


class CoordinateTransformer:
    """
    坐标转换器

    实现完整的坐标转换链：
    像素坐标 -> 相机坐标系 -> 机器人基座坐标系
    """

    def __init__(
        self,
        camera_matrix: np.ndarray,
        distortion_coeffs: np.ndarray,
        camera_to_base_transform: Transform3D,
        image_size: Tuple[int, int]
    ):
        """
        初始化坐标转换器

        Args:
            camera_matrix: 相机内参矩阵 3x3 [[fx, 0, cx], [0, fy, cy], [0, 0, 1]]
            distortion_coeffs: 畸变系数 [k1, k2, p1, p2, k3]
            camera_to_base_transform: 相机到机器人基座的变换
            image_size: 图像尺寸 (width, height)
        """
        self.K = camera_matrix
        self.dist = distortion_coeffs
        self.T_cam_to_base = camera_to_base_transform
        self.image_size = image_size

        # 提取内参
        self.fx = self.K[0, 0]
        self.fy = self.K[1, 1]
        self.cx = self.K[0, 2]
        self.cy = self.K[1, 2]

    def pixel_to_camera(
        self,
        pixel_x: float,
        pixel_y: float,
        depth_mm: float,
        undistort: bool = True
    ) -> Point3D:
        """
        像素坐标转换到相机坐标系

        Args:
            pixel_x: 像素x坐标
            pixel_y: 像素y坐标
            depth_mm: 深度值(mm) - 物体到相机平面的距离
            undistort: 是否进行畸变校正

        Returns:
            相机坐标系下的3D点 (mm)
        """
        if undistort:
            # 畸变校正
            points = np.array([[[pixel_x, pixel_y]]], dtype=np.float32)
            undistorted = cv2.undistortPoints(
                points, self.K, self.dist, None, self.K
            )
            px, py = undistorted[0, 0]
        else:
            px, py = pixel_x, pixel_y

        # 针孔相机模型反投影
        # X_cam = (u - cx) * Z / fx
        # Y_cam = (v - cy) * Z / fy
        # Z_cam = Z
        x_cam = (px - self.cx) * depth_mm / self.fx
        y_cam = (py - self.cy) * depth_mm / self.fy
        z_cam = depth_mm

        return Point3D(x=x_cam, y=y_cam, z=z_cam)

    def camera_to_robot_base(self, point_cam: Point3D) -> Point3D:
        """
        相机坐标系转换到机器人基座坐标系

        Args:
            point_cam: 相机坐标系下的点

        Returns:
            机器人基座坐标系下的点
        """
        return self.T_cam_to_base.apply(point_cam)

    def pixel_to_robot_base(
        self,
        pixel_x: float,
        pixel_y: float,
        depth_mm: float,
        undistort: bool = True
    ) -> Point3D:
        """
        像素坐标直接转换到机器人基座坐标系

        Args:
            pixel_x: 像素x坐标
            pixel_y: 像素y坐标
            depth_mm: 深度值(mm)
            undistort: 是否畸变校正

        Returns:
            机器人基座坐标系下的点 (mm)
        """
        # 步骤1: 像素 -> 相机坐标
        point_cam = self.pixel_to_camera(pixel_x, pixel_y, depth_mm, undistort)

        # 步骤2: 相机坐标 -> 机器人基座坐标
        point_base = self.camera_to_robot_base(point_cam)

        return point_base

    def estimate_depth_from_plane(
        self,
        pixel_x: float,
        pixel_y: float,
        plane_z_in_base: float
    ) -> float:
        """
        从已知平面高度估算深度

        假设物体位于已知高度的平面上（如工作台），反推深度值

        Args:
            pixel_x: 像素x坐标
            pixel_y: 像素y坐标
            plane_z_in_base: 平面在机器人基座坐标系中的Z高度(mm)

        Returns:
            估算的深度值 (mm)
        """
        # 这是一个简化实现，假设平面平行于XY平面
        # 更精确的实现需要求解射线与平面的交点

        # 相机原点在基座坐标系中的位置
        cam_origin_base = Point3D(
            x=self.T_cam_to_base.translation[0],
            y=self.T_cam_to_base.translation[1],
            z=self.T_cam_to_base.translation[2]
        )

        # 计算相机到平面的垂直距离作为深度估计
        # 这是简化版本，实际应该考虑相机倾角
        depth_estimate = abs(cam_origin_base.z - plane_z_in_base)

        return depth_estimate

    def robot_base_to_camera(self, point_base: Point3D) -> Point3D:
        """
        机器人基座坐标系转换到相机坐标系（逆变换）

        Args:
            point_base: 机器人基座坐标系下的点

        Returns:
            相机坐标系下的点
        """
        # 计算逆变换: T_base_to_cam = T_cam_to_base^(-1)
        R_inv = self.T_cam_to_base.rotation.T
        t_inv = -R_inv @ self.T_cam_to_base.translation

        p = point_base.to_array().reshape(3, 1)
        transformed = R_inv @ p + t_inv.reshape(3, 1)

        return Point3D.from_array(transformed.flatten())

    def camera_to_pixel(
        self,
        point_cam: Point3D,
        distort: bool = False
    ) -> Tuple[float, float]:
        """
        相机坐标系投影到像素坐标（正向投影）

        Args:
            point_cam: 相机坐标系下的点
            distort: 是否应用畸变（通常不需要）

        Returns:
            (pixel_x, pixel_y)
        """
        # 针孔相机投影
        u = self.fx * point_cam.x / point_cam.z + self.cx
        v = self.fy * point_cam.y / point_cam.z + self.cy

        if distort:
            # TODO: 实现畸变模型
            pass

        return (u, v)

    def validate_transform(self, point_base: Point3D, tolerance_mm: float = 5.0) -> Dict[str, Any]:
        """
        验证坐标转换的往返精度

        Args:
            point_base: 机器人基座坐标系中的测试点
            tolerance_mm: 允许的误差(mm)

        Returns:
            包含验证结果的字典
        """
        # 正向: 基座 -> 相机 -> 像素
        point_cam = self.robot_base_to_camera(point_base)
        pixel_x, pixel_y = self.camera_to_pixel(point_cam)

        # 反向: 像素 -> 相机 -> 基座
        depth = point_cam.z
        reconstructed = self.pixel_to_robot_base(pixel_x, pixel_y, depth, undistort=False)

        # 计算误差
        error = np.linalg.norm(
            point_base.to_array() - reconstructed.to_array()
        )

        return {
            "original": point_base,
            "reconstructed": reconstructed,
            "error_mm": float(error),
            "passed": error < tolerance_mm,
            "intermediate": {
                "camera_coords": point_cam,
                "pixel_coords": (pixel_x, pixel_y)
            }
        }

    def to_dict(self) -> Dict[str, Any]:
        """导出为字典（用于保存配置）"""
        return {
            "camera_matrix": self.K.tolist(),
            "distortion_coeffs": self.dist.tolist(),
            "camera_to_base_transform": {
                "rotation": self.T_cam_to_base.rotation.tolist(),
                "translation": self.T_cam_to_base.translation.tolist()
            },
            "image_size": self.image_size
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CoordinateTransformer":
        """从字典加载配置"""
        transform = Transform3D(
            rotation=np.array(data["camera_to_base_transform"]["rotation"]),
            translation=np.array(data["camera_to_base_transform"]["translation"])
        )

        return cls(
            camera_matrix=np.array(data["camera_matrix"]),
            distortion_coeffs=np.array(data["distortion_coeffs"]),
            camera_to_base_transform=transform,
            image_size=tuple(data["image_size"])
        )


# 导入cv2用于畸变校正
try:
    import cv2
except ImportError:
    cv2 = None
    import warnings
    warnings.warn("OpenCV not available, undistortion will be disabled")
