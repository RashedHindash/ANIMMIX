# ANIMMIX
![image alt](https://github.com/RashedHindash/ANIMMIX/blob/main/ANIMMIX%20Logo.png)
Modern animation workflow toolkit for 3ds Max 2026+. Professional-grade tools for character animation with full support for complex rigs. Inspired by Maya's animation ecosystem. AI-assisted

![image alt](https://github.com/RashedHindash/ANIMMIX/blob/main/Screenshot%202025-12-31%20161519.png)

Tool Description and Current Limitations:

  The tool is currently a work in progress and continues to evolve through iterative testing and refinement.
  At its current stage, the tool supports standard bone-based and custom rig setups. CAT and Biped rigs are not supported at this time, as they rely on internal animation systems that require specialized handling. Support for CAT and Biped rigs is planned for a future release once the core feature set has been finalized and stabilized.

Setup Check and Snapshot Workflow:

  1- The tool relies on controller naming conventions to correctly identify left and right sides of a character.

  2- Controllers must include _L and _R in their names to allow the system to distinguish between left and right elements during operations such as mirroring.

  This naming requirement is validated during the Setup Check, which ensures that the rig follows the expected structure before using pose-based tools.


Creating and Saving a Snapshot (Pose State):

  1- Select all relevant animation controllers for the character.
  
  2- Click on the Snapshot Tool within the interface.
  
  3- Provide a clear and descriptive name for the snapshot.
  
  4- Save the snapshot to store the current pose state for later recall, comparison, or restoration.
  
  This snapshot-based approach allows animators to preserve pose states safely, allows the other tools to function correctly and work non-destructively while refining animation.
