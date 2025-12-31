<h1 align="center">ANIMMIX</h1>
<h3 align="center">Animate Your Way</h3>

---

<p align="center">
<img alt="Logo Banner" src="https://github.com/RashedHindash/ANIMMIX/blob/main/ANIMMIX%20Logo.png"/>
Modern animation workflow toolkit for 3ds Max 2026+. Professional-grade tools for character animation with full support for complex rigs. Inspired by Maya's animation ecosystem. AI-assisted

---

<p align="center">
<img alt="Tool" src="https://github.com/RashedHindash/ANIMMIX/blob/main/Screenshot%202025-12-31%20161519.png"/>

---

Tool Description and Current Limitations:

  The tool is currently a work in progress and continues to evolve through iterative testing and refinement.
  At its current stage, the tool supports standard bone-based and custom rig setups. CAT and Biped rigs are not supported at this time, as they rely on internal animation systems that require specialized handling. Support for CAT and Biped rigs is planned for a future release once the core feature set has been finalized and stabilized.
  
 ---

<h3 align="center">How To Install</h3>

1- Download the basic file.

2- Unzip the file and place it in a location that 3ds Max can access.

3- Open 3ds Max.

4- Go to Scripting → Open Script.

5- Navigate to the folder containing the script and select it.

Important: Make sure the image file is in the same folder as the script; otherwise, the image will not load.

 ---

<h3 align="center">Setup</h3>


  1- The tool relies on controller naming conventions to correctly identify left and right sides of a character.

  2- Controllers must include _L and _R in their names to allow the system to distinguish between left and right elements during operations such as mirroring.

  This naming requirement is validated during the Setup Check, which ensures that the rig follows the expected structure before using pose-based tools.


 ---

<h3 align="center">Workflow</h3>

  1- Select all relevant animation controllers for the character.
  
  2- Click on the Snapshot Tool within the interface.
  
  3- Provide a clear and descriptive name for the snapshot.
  
  4- Save the snapshot to store the current pose state for later recall, comparison, or restoration.
  
  This snapshot-based approach allows animators to preserve pose states safely, allows the other tools to function correctly and work non-destructively while refining animation.
