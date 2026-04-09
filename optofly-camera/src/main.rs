mod buffer;
mod capture;
mod config;
mod encoder;
mod metadata;

use clap::Parser;

#[derive(Parser, Debug)]
#[command(name = "optofly-camera", about = "High-speed XIMEA camera capture")]
struct Args {
    /// Path to config TOML file
    #[arg(short, long, default_value = "configs/config.toml")]
    config: String,

    /// Save folder for video output (overrides config)
    #[arg(short, long)]
    save_folder: Option<String>,

    /// Log level
    #[arg(short, long, default_value = "info")]
    log_level: String,
}

fn main() {
    let args = Args::parse();
    env_logger::Builder::new()
        .filter_level(args.log_level.parse().unwrap_or(log::LevelFilter::Info))
        .init();

    log::info!("optofly-camera starting with config: {}", args.config);

    let cfg = config::AppConfig::load(&args.config, args.save_folder.as_deref())
        .expect("Failed to load config");
    log::info!(
        "Config loaded: {}x{} @ {}fps, buffer {:.1}s, save to: {}",
        cfg.width, cfg.height, cfg.fps, cfg.max_recording_time, cfg.save_folder
    );

    if let Err(e) = capture::run(cfg) {
        log::error!("Capture failed: {}", e);
        std::process::exit(1);
    }

    log::info!("optofly-camera exiting cleanly");
}
