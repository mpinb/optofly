use serde::Deserialize;
use std::fs;

#[derive(Debug, Deserialize)]
struct TomlRoot {
    camera: CameraToml,
    zmq: ZmqToml,
}

#[derive(Debug, Deserialize)]
struct CameraToml {
    resolution: [u32; 2],
    fps: f32,
    exposure_time: f32,
    offset_x: Option<u32>,
    offset_y: Option<u32>,
    serial: Option<u32>,
    pre_trigger_time: f32,
    post_trigger_time: f32,
    save_folder: Option<String>,
}

#[derive(Debug, Deserialize)]
struct ZmqToml {
    trigger_port: u16,
    trigger_topic: String,
}

#[derive(Debug)]
pub struct AppConfig {
    pub width: u32,
    pub height: u32,
    pub fps: f32,
    pub exposure_us: f32,
    pub offset_x: u32,
    pub offset_y: u32,
    pub serial: u32,
    pub pre_trigger_time: f32,
    pub post_trigger_time: f32,
    pub save_folder: String,
    pub zmq_trigger_address: String,
    pub zmq_trigger_topic: String,
}

impl AppConfig {
    pub fn load(path: &str, save_folder_override: Option<&str>) -> Result<Self, String> {
        let contents = fs::read_to_string(path)
            .map_err(|e| format!("Cannot read config {}: {}", path, e))?;
        let root: TomlRoot = toml::from_str(&contents)
            .map_err(|e| format!("Cannot parse config: {}", e))?;

        let cam = root.camera;
        let zmq = root.zmq;

        let save_folder = save_folder_override
            .map(String::from)
            .or(cam.save_folder)
            .unwrap_or_else(|| "camera_videos".to_string());

        fs::create_dir_all(&save_folder)
            .map_err(|e| format!("Cannot create save folder {}: {}", save_folder, e))?;

        Ok(AppConfig {
            width: cam.resolution[0],
            height: cam.resolution[1],
            fps: cam.fps,
            exposure_us: cam.exposure_time,
            offset_x: cam.offset_x.unwrap_or(0),
            offset_y: cam.offset_y.unwrap_or(0),
            serial: cam.serial.unwrap_or(0),
            pre_trigger_time: cam.pre_trigger_time,
            post_trigger_time: cam.post_trigger_time,
            save_folder,
            zmq_trigger_address: format!("tcp://localhost:{}", zmq.trigger_port),
            zmq_trigger_topic: zmq.trigger_topic,
        })
    }

    pub fn pre_trigger_frames(&self) -> usize {
        (self.fps * self.pre_trigger_time) as usize
    }

    pub fn post_trigger_frames(&self) -> usize {
        (self.fps * self.post_trigger_time) as usize
    }

    pub fn buffer_capacity(&self) -> usize {
        self.pre_trigger_frames() + self.post_trigger_frames()
    }

    pub fn frame_bytes(&self) -> usize {
        self.width as usize * self.height as usize
    }
}
