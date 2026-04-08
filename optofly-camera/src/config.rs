pub struct AppConfig;

impl AppConfig {
    pub fn load(_path: &str, _save_folder: Option<&str>) -> Result<Self, String> {
        Ok(AppConfig)
    }
}
