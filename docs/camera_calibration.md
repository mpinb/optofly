# Camera Calibration Tutorial

## Overview

This tutorial will guide you through calibrating cameras for 3D tracking using ROS2. While the setup is involved, the process gives you much better control and higher quality results than alternative methods.

### What is Camera Calibration?

Camera calibration consists of two parts:

- **Intrinsic Calibration** - Internal camera parameters: focal length, principal point, and lens distortion coefficients. This describes how the camera projects 3D points onto its 2D image plane.
- **Extrinsic Calibration** - Camera pose in world coordinates: rotation and translation that describe the camera's position and orientation relative to a reference frame or other cameras.

This tutorial focuses on **intrinsic calibration**. While `Braid` includes both components, its intrinsic calibration tends to be slow and buggy. Using ROS2 provides more control and better results.

### What You'll Need

- Ubuntu Linux (this tutorial uses Ubuntu 24.04)
- Camera(s) with GenICam/GigE Vision support (e.g., Basler cameras)
- A printed checkerboard calibration pattern
- About 1-2 hours for the complete setup

### The Big Picture

The calibration process involves three main components:

1. **ROS2** - A robotics middleware framework that handles communication between different software components
2. **Camera Driver** - Software that connects to your camera and publishes images
3. **Camera Calibrator** - A tool that analyzes the images and computes calibration parameters

Don't worry if you're unfamiliar with these tools - this guide will walk you through everything step by step.

---

## Part 1: Installing ROS2

ROS2 (Robot Operating System 2) is the framework that ties everything together. We'll install the "Jazzy" version, which is the latest long-term support release.

### Step 1: Set Up Ubuntu Repositories

First, ensure your system can access the necessary software repositories:

```bash
sudo apt install software-properties-common
sudo add-apt-repository universe
```

**What this does:** Enables the Ubuntu Universe repository, which contains additional community-maintained software.

### Step 2: Add ROS2 Repository

Now add the official ROS2 package repository:

```bash
sudo apt update && sudo apt install curl -y
export ROS_APT_SOURCE_VERSION=$(curl -s https://api.github.com/repos/ros-infrastructure/ros-apt-source/releases/latest | grep -F "tag_name" | awk -F\" '{print $4}')
curl -L -o /tmp/ros2-apt-source.deb "https://github.com/ros-infrastructure/ros-apt-source/releases/download/${ROS_APT_SOURCE_VERSION}/ros2-apt-source_${ROS_APT_SOURCE_VERSION}.$(. /etc/os-release && echo ${UBUNTU_CODENAME:-${VERSION_CODENAME}})_all.deb"
sudo dpkg -i /tmp/ros2-apt-source.deb
```

**What this does:** Downloads and installs the ROS2 package repository configuration, allowing your system to find and install ROS2 packages.

### Step 3: Install Development Tools

```bash
sudo apt update && sudo apt install ros-dev-tools
```

**What this does:** Installs essential tools for building and managing ROS2 packages (like `colcon` and `rosdep`).

### Step 4: Install ROS2 Desktop

```bash
sudo apt update
sudo apt upgrade
sudo apt install ros-jazzy-desktop
```

**What this does:** Installs ROS2 Jazzy with desktop tools, including visualization and simulation packages. This may take several minutes.

### Step 5: Install Camera Calibration Dependencies

```bash
sudo apt install ros-jazzy-camera-calibration-parsers
sudo apt install ros-jazzy-camera-info-manager
sudo apt install ros-jazzy-launch-testing-ament-cmake
```

**What this does:** Installs additional packages needed for camera calibration.

### Step 6: Set Up Your Environment

Every time you open a new terminal and want to use ROS2, you need to "source" the setup file:

```bash
source /opt/ros/jazzy/setup.bash
```

**Optional but recommended:** Add this to your `.bashrc` file so it runs automatically in new terminals:

```bash
echo "source /opt/ros/jazzy/setup.bash" >> ~/.bashrc
```

---

## Part 2: Creating a ROS2 Workspace

A workspace is a directory where you'll build and manage ROS2 packages. Think of it as a project folder.

### Step 1: Create the Workspace Directory

```bash
mkdir -p ~/ros2_ws/src
cd ~/ros2_ws/src
```

**What this does:** Creates a new directory structure for your workspace. The `src` folder will contain source code for packages you'll build.

### Step 2: Download Required Packages

We need two packages:
- `image_pipeline` - Contains the camera calibration tools
- `camera_aravis2` - Driver for GenICam-compatible cameras (like Basler)

```bash
git clone -b jazzy git@github.com:ros-perception/image_pipeline.git
git clone https://github.com/FraunhoferIOSB/camera_aravis2.git
```

**Troubleshooting:** If the first command fails with an SSH error, use HTTPS instead:
```bash
git clone -b jazzy https://github.com/ros-perception/image_pipeline.git
```

### Step 3: Install Package Dependencies

Move back to the workspace root and install dependencies:

```bash
cd ~/ros2_ws
rosdep install -i --from-path src --rosdistro jazzy -y
```

**What this does:** Automatically finds and installs all dependencies needed by the packages in your workspace. This may download and install several packages.

### Step 4: Build the Workspace

```bash
colcon build
```

**What this does:** Compiles all packages in your workspace. This will take several minutes. You'll see progress messages as each package builds.

**Note:** If you see warnings during the build, that's usually okay. Errors will clearly state "failed" and stop the build process.

### Step 5: Source Your Workspace

After building, you need to tell your terminal where to find the newly built packages:

```bash
source install/setup.bash
```

**Important:** You need to run this command in every new terminal where you want to use your workspace. You can add it to your `.bashrc`:

```bash
echo "source ~/ros2_ws/install/setup.bash" >> ~/.bashrc
```

### Step 6: Set Up Launch File

Copy the camera launch file to the correct location:

```bash
cp /path/to/your/basler_pylon.launch.py ~/ros2_ws/install/camera_aravis2/share/camera_aravis2/launch/
```

**What this does:** Places a pre-configured launch file that makes it easy to start your camera with the right settings.

**Note:** Replace `/path/to/your/basler_pylon.launch.py` with the actual path to your launch file. If you don't have this file yet, you can create one later or use the default launch files that come with the package.

---

## Part 3: Installing Camera Driver Software

### Install Aravis GenICam Library

```bash
sudo apt install aravis-tools aravis-tools-cli
```

**What this does:** Installs the Aravis library and command-line tools that allow your computer to communicate with GenICam-compatible cameras.

### Test Your Camera Connection

Verify your camera is detected:

```bash
arv-tool-0.8
```

**Expected output:** A list of connected cameras showing their model numbers and IDs, like:
```
Basler-267601CA4750-40080153
Basler-267601CA4756-40080159
...
```

**Troubleshooting:**
- **No cameras listed?** Check USB/network connections and power
- **Permission denied?** You may need to add your user to the appropriate group or run with sudo
- **Command not found?** Make sure you installed aravis-tools correctly

---

## Part 4: Performing the Calibration

Now comes the actual calibration process. You'll need to have your camera connected and a checkerboard pattern ready.

### Preparation

#### 1. Prepare Your Checkerboard Pattern

- **Print the pattern:** Use a high-quality printer and thick paper or mount it on cardboard
- **Important:** ROS counts **internal corners**, not squares
  - An 8×6 checkerboard (8 squares wide, 6 tall) has **7×5 internal corners**
  - Count the points where four squares meet
- **Measure square size:** Use a ruler to measure one square in meters (e.g., 15mm = 0.015m)
- **Keep it flat:** Mount the pattern on something rigid (foam board or stiff cardboard works well)

**Tip:** A slightly larger checkerboard (8×10 squares) gives better results as you can tilt it more without losing detection.

#### 2. Set Up Your Environment

You'll need **two terminal windows** open simultaneously. In both terminals, run:

```bash
cd ~/ros2_ws
source install/setup.bash
```

**Why two terminals?** One runs the camera driver (publishing images), the other runs the calibrator (analyzing images). They communicate through ROS2 topics.

### Launch the Camera Node

#### Step 1: Find Your Camera ID

In the first terminal, list all connected cameras:

```bash
arv-tool-0.8
```

Look for your camera's full ID (e.g., `Basler-267601CA4750-40080153`). You'll need the last part after the final dash (e.g., `40080153`).

#### Step 2: Start the Camera Driver

```bash
ros2 launch camera_aravis2 basler_pylon.launch.py guid:="Basler-40080153"
```

**Replace** `Basler-40080153` with your camera's actual ID.

**What to expect:**
- Several INFO messages about camera initialization
- "Done initializing" message at the end
- The terminal will continue running (don't close it!)
- If you see errors, check the troubleshooting section below

**Key information from the output:**
```
Sensor Size:         1936x1216
Pixel Format:        Mono8
Image Size:          1920x1200
Exposure Time (us):  10000.000000
Frame Rate (Hz):     30.000000
```

#### Step 3: Verify Topics Are Publishing

In the second terminal, check that the camera is publishing data:

```bash
ros2 topic list
```

**Expected output:**
```
/basler_camera/camera_info
/basler_camera/image_raw
/parameter_events
/rosout
```

You should see at least the first two topics. If not, something went wrong with the camera driver.

**Optional verification:** Check the image stream:
```bash
ros2 topic hz /basler_camera/image_raw
```
This should show approximately 30 Hz if your camera is set to 30 FPS.

### Launch the Calibrator

In the second terminal, start the camera calibrator:

```bash
ros2 run camera_calibration cameracalibrator --size 7x5 --square 0.015 \
  --ros-args -r image:=/basler_camera/image_raw \
  -r camera/set_camera_info:=/basler_camera/set_camera_info \
  -r camera_info:=/basler_camera/camera_info
```

**Adjust the parameters for your checkerboard:**
- `--size 7x5`: Replace with your internal corner count (width × height)
- `--square 0.015`: Replace with your square size in meters

**What the flags mean:**
- `-r image:=/basler_camera/image_raw` - Subscribe to the camera's image topic
- `-r camera/set_camera_info:=...` - Connect to the service that saves calibration data
- `-r camera_info:=...` - Subscribe to camera info messages

**What to expect:**
- A window will open showing your camera feed
- Four progress bars on the right: X, Y, Size, Skew
- The checkerboard should be automatically detected if visible

### Collecting Calibration Samples

This is the most important part of the process. Good calibration requires diverse samples.

#### What the Progress Bars Mean

- **X bar**: Coverage across the horizontal field of view (left to right)
- **Y bar**: Coverage across the vertical field of view (top to bottom)  
- **Size bar**: Variation in distance (near and far from camera)
- **Skew bar**: Variation in board angle/orientation

#### Best Practices for Collection

1. **Start in the center:** Hold the board flat in the center of the view until it's detected
2. **Move systematically:**
   - Top-left corner
   - Top-right corner
   - Bottom-left corner
   - Bottom-right corner
   - Back to center
3. **Vary the distance:**
   - Hold the board close to the camera
   - Hold it far away
   - Use intermediate distances
4. **Tilt the board:**
   - Rotate it around all three axes
   - Try diagonal orientations
   - Avoid going so extreme that corners get cut off

#### How Many Samples?

- **Minimum:** About 40 samples
- **Recommended:** 50-70 samples for best results
- **Goal:** Fill all four bars to at least 80%

**Tip:** The calibrator automatically captures images when it detects the checkerboard, so you don't need to press anything. Just move the board smoothly and pause briefly at each position.

#### When the Board Isn't Detected

If the colored overlay doesn't appear on the checkerboard:
- Ensure better lighting (avoid glare and shadows)
- Hold the board steadier
- Make sure all internal corners are visible
- Check that you specified the correct checkerboard size
- Try adjusting the exposure in your camera launch file

### Running the Calibration

#### Step 1: Initiate Calibration

Once all bars are sufficiently filled (you don't need 100%), the **CALIBRATE** button will become active. Click it.

**What happens:**
- The calibrator processes all collected samples
- This may take 30 seconds to a few minutes depending on the number of samples
- A progress indicator shows the computation status

#### Step 2: Review Results

After calibration completes, you'll see:

- **Reprojection error:** This is the most important metric
  - **Good:** < 0.5 pixels
  - **Acceptable:** 0.5-1.0 pixels
  - **Poor:** > 1.0 pixels (consider recalibrating)
  
- **Camera matrix (K):** The intrinsic parameters
- **Distortion coefficients (D):** Lens distortion parameters

If the reprojection error is high:
1. Click **CALIBRATE** again (sometimes it improves on a second pass)
2. If still poor, close the calibrator and collect new samples with better coverage

#### Step 3: Save the Calibration

Click **SAVE** to save the raw calibration data. This creates a file called `calibrationdata.tar.gz` in the `/tmp/` folder.

**Note:** Don't click "COMMIT" - we'll manually copy the calibration file to where it's needed.

---

## Part 5: Using the Calibration with Braid

### Extract and Copy Calibration Files

1. **Navigate to the temp folder:**
   ```bash
   cd /tmp
   ```

2. **Extract the calibration archive:**
   ```bash
   tar -xzf calibrationdata.tar.gz
   ```
   
   Alternatively, you can double-click the file in your file manager to extract it.

3. **Find the calibration file:**
   Look for `ost.yaml` in the extracted files. This contains your calibration data.

4. **Copy to Strand-cam config directory:**
   ```bash
   mkdir -p ~/.config/strand-cam/camera_info
   cp ost.yaml ~/.config/strand-cam/camera_info/Basler-40080153.yaml
   ```
   
   **Replace** `Basler-40080153.yaml` with your actual camera ID.

### Edit the Camera Name

Open the copied YAML file in a text editor (either `nano` from the terminal, or the default Ubuntu one):

```bash
nano ~/.config/strand-cam/camera_info/Basler-40080153.yaml
```

Find the line that says:
```yaml
camera_name: narrow_stereo
```

Change it to match your camera ID:
```yaml
camera_name: Basler-40080153
```

Save and close the file (in nano: Ctrl+O, Enter, Ctrl+X).

### Repeat for All Cameras

If you have multiple cameras, repeat the entire calibration process for each one:

1. Launch the camera driver with each camera's GUID
2. Run the calibrator
3. Collect samples (40-50 per camera)
4. Save and extract the calibration
5. Copy the `ost.yaml` file with the appropriate camera name

**Tip:** Keep the checkerboard in similar positions for all cameras to ensure consistent calibration quality.

---

## Troubleshooting

### Camera Driver Issues

**Problem:** Camera not detected or won't start

**Solutions:**
- Verify camera is powered and connected
- Check USB 3.0 connection (blue USB port)
- Try a different USB port or cable
- Run `arv-tool-0.8` to verify camera is visible
- Check camera permissions: `sudo chmod 666 /dev/bus/usb/*/*`

**Problem:** "USB3Vision write_memory error (write-protect)"

**Solution:** This is usually just a warning and can be ignored. The camera should still work.

### Calibrator Issues

**Problem:** Calibrator can't find camera service

**Solution:** Verify you're using the correct topic remapping:
```bash
ros2 service list | grep set_camera_info
```
Should show `/basler_camera/set_camera_info`

**Problem:** Checkerboard not detected

**Solutions:**
- Verify correct checkerboard size (count internal corners!)
- Improve lighting - avoid shadows and glare
- Hold the board steadier
- Make sure the entire board is in view
- Check that the board is actually a checkerboard pattern (not distorted by printing)

**Problem:** High reprojection error (> 1.0 pixels)

**Solutions:**
- Collect more diverse samples
- Ensure good coverage in all four progress bars
- Check that the checkerboard was flat and not warped
- Verify you measured the square size correctly
- Try recalibrating with a fresh sample set

### General ROS2 Issues

**Problem:** `ros2` commands not found

**Solution:** Source the setup file:
```bash
source /opt/ros/jazzy/setup.bash
source ~/ros2_ws/install/setup.bash
```

**Problem:** Build errors with `colcon build`

**Solutions:**
- Run `rosdep install -i --from-path src --rosdistro jazzy -y` again
- Check error messages for missing dependencies
- Try cleaning and rebuilding: `rm -rf build install log && colcon build`

---

## Summary

You've successfully:
1. ✅ Installed ROS2 Jazzy
2. ✅ Built a ROS2 workspace with camera drivers
3. ✅ Calibrated your camera(s) intrinsically
4. ✅ Saved calibration files for use with Braid

Your cameras are now ready for 3D tracking. The intrinsic calibration files you created will be automatically loaded by Braid when you run your tracking experiments.

### Next Steps

- Perform extrinsic calibration in Braid to determine camera positions relative to each other
- Set up your 3D tracking volume
- Start collecting behavioral data

### Useful Commands Reference

```bash
# List available cameras
arv-tool-0.8

# Launch a camera
ros2 launch camera_aravis2 basler_pylon.launch.py guid:="Basler-XXXXXXXX"

# List active topics
ros2 topic list

# Check topic frequency
ros2 topic hz /basler_camera/image_raw

# List services
ros2 service list

# View calibration file
cat ~/.config/strand-cam/camera_info/Basler-XXXXXXXX.yaml
```

---

**Need help?** Check the official documentation:
- [ROS2 Documentation](https://docs.ros.org/en/jazzy/)
- [Camera Aravis2 GitHub](https://github.com/FraunhoferIOSB/camera_aravis2)
- [Aravis Documentation](https://aravisproject.github.io/aravis/)