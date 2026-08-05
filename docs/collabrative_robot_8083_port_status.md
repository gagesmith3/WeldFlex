COLLABORATIVE ROBOT 8083 PORT STATUS

FEEDBACK（V3.9.8）

I

Contents

Contents
1

Overview of the robot 8083 port status feedback ................................................. 1

2

Port 8083 Status Feedback Operation Instructions ............................................... 2
2.1 Communication protocol format definition................................................... 2
2.2 Description of the 8083 port status feedback data ......................................... 2
2.2.1 A summary table of data contents ...................................................... 2
2.2.2 Data Content - Structure Definition ................................................. 14

Appendix 1 Error Code Correspondence.................................................................. 16

1 / 16

User Manual

1 Overview of the robot 8083 port status feedback
The user can establish a connection with the 8083 port of the robot controller
through TCP/TP, and the 8083 port sends a data frame every 100ms by default after the
connection is established, and the data frame contains some real-time status feedback
data of the robot for the user's use, and the communication topology diagram is shown
in Figure 1-1. In addition, the status feedback cycle is user-configurable, and the status
feedback sending cycle of port 8083 can be set in the system settings-> maintenance
mode, and the setting range is 8-100ms. As shown in Figure 1-2.
Robot Status Feedback

TCP/Ip communication（8083 port）
Network cable

Figure 1-1 Topology of the robot 8083 port status feedback communication

Figure 1-2 Setting the status feedback cycle of port 8083 of the robot

2 / 16

User Manual

2 Port 8083 Status Feedback Operation Instructions
2.1 Communication protocol format definition
Table 2-1 describes the data frame format of port 8083, which can be unpacked
and verified in the following formats:
Table 2-1 Port 8083 data feedback protocol format
Frame

Frame Count

Header
0x5A5A

The Length of

Data Content

Sum Check

DATA

CHECKSUM

The Data
CNT

LEN

Each of these items is described in detail:
（1） Frame Header: The convention is 0x5A5A, and the data format is uint16_t
（2） Frame Count: Loop count 0-255, data format uint8_t
（3） Data Length: The length of the data content, the data format, uint16_t
（4） Data content: The real-time status feedback data of the robot, see section
2.2 for a detailed description
（5） Sum check: Sum all bytes from the frame header to the data content, and
the data format is uint16_t

2.2 Description of the 8083 port status feedback data
2.2.1 A summary table of data contents
After the data frame verification is completed, the state feedback data of the robot
at the current moment can be obtained according to the data content, and the specific
data content summary table is shown in Table 2-2.

3 / 16

User Manual

Table 2-2 Port 8083 status data content is summarized
Serial

Name

Variable Name

Number
1

The running

program_state

Data

Number

Type

of Bytes

uint8_t

1

status of the

Detailed Description

1 - Stop; 2 - Run; 3Suspended; 4- Drag

program
2

Fault codes

error_code

uint8_t

1

Table 2-3 describes the
error code

3

Robot mode

robot_mode

uint8_t

1

0 - automatic mode, 1 manual mode; 2- Drag
mode

4

1 axis current

jt_cur_pos[0]

double

8

[deg]

jt_cur_pos[1]

double

8

[deg]

jt_cur_pos[2]

double

8

[deg]

jt_cur_pos[3]

double

8

[deg]

joint position
5

2-axis current
joint position

6

3. The current
joint position
of the axis

7

4-axis
current joint
position

4 / 16

User Manual

Table 2-2 (continued)
Serial

Name

Variable Name

Data

Number

Type

of Bytes

jt_cur_pos[3]

double

8

[deg]

jt_cur_pos[4]

double

8

[deg]

jt_cur_pos[5]

double

8

[deg]

tl_cur_pos[0]

double

8

[mm]

tl_cur_pos[1]

double

8

[mm]

tl_cur_pos[2]

double

8

[mm]

Number
7

4-axis

Detailed Description

current
joint
position
8

5 axis
current
joint
position

9

6 axis
current
joint
position

10

The tool's
current
position x

11

The
current
position
of the
tool y

12

The tool's
current
position z

5 / 16

User Manual

Table 2-2 (continued)
Serial

Name

Variable Name

Data

Number

Type

of Bytes

tl_cur_pos[3]

double

8

[deg]

tl_cur_pos[4]

double

8

[deg]

tl_cur_pos[5]

double

8

[deg]

toolNum

int

4

-

jt_cur_tor[0]

double

8

[N·m]

jt_cur_tor[1]

double

8

[N·m]

jt_cur_tor[2]

double

8

[N·m]

jt_cur_tor[3]

double

8

[N·m]

Number
13

Tool

Detailed Description

current
pose a
14

Tool
current
pose b

15

Tool's
current
pose c

16

Tool
number

17

1 axle
current
torque

18

2-axis
current
torque

19

3-axis
current
torque

20

Current
torque on
4 axes

6 / 16

User Manual

Table 2-2 (continued)
Serial

Name

Variable Name

Data

Number

Detailed

Type

of Bytes

Description

jt_cur_tor[4]

double

8

[N·m]

jt_cur_tor[5]

double

8

[N·m]

program_name[20]

char

20

-

prog_total_line

uint8_t

1

-

prog_cur_line

uint8_t

1

-

cl_dgt_output_h

uint8_t

1

-

cl_dgt_output_l

uint8_t

1

-

tl_dgt_output_l

uint8_t

1

Only bit0-bit1 works

Number
21

5-axis current
torque

22

6-axis current
torque

23

Name of the
running
program

24

The total
number of rows
running the
program

25

Run the current
line of the
program

26

The digital IO
output of the
control box is
15-8

27

The digital IO
output of the
control box is
7-0

28

The tool digital
IO output is 7-0

7 / 16

User Manual

Table 2-2 (continued)
Serial

Name

Variable Name

Data

Number

Type

of Bytes

cl_dgt_input_h

uint8_t

1

-

cl_dgt_input_l

uint8_t

1

-

tl_dgt_input_l

uint8_t

1

Only bit0-bit1 works

FT_data[0]

double

8

[N]

FT_data[1]

double

8

[N]

FT_data[2]

double

8

[N]

FT_data[3]

double

8

[N·m]

FT_data[4]

double

8

[N·m]

Number
29

The digital IO

Detailed Description

input of the
control box is
15-8
30

The digital IO
input of the
control box is 70

31

The tool digital
IO input is 7-0

32

Force/Torque
Transducer Data
Fx

33

Force/Torque
Sensor Data Fy

34

Force/Torque
Transducer Data
Fz

35

Force/Torque
Transducer Data
Tx

36

Force/Torque
Transducer Data
Ty

8 / 16

User Manual

Table 2-2 (continued)
Serial

Name

Variable Name

Data

Number

Detailed

Type

of Bytes

Description

FT_data[5]

double

8

[N·m]

FT_ActStatus

uint8_t

1

0 - reset, 1 - activate

EmergencyStop

uint8_t

1

1 - emergency stop,

Number
37

Force/Torque
Transducer
Data Tz

38

Force/torque
sensor
activation
status

39

Emergency
stop signs

40

Robot
movement in
place signal

0 - none
robot_motion_done

int

4

1 - in place, 0 - not
in place

9 / 16

User Manual

Table 2-2 (continued)
Serial

Name

Variable Name

Number

Data

Numb

Type

er of

Detailed Description

Bytes
41

The

gripper_motion_done

uint8_t

1

robotiq: 0-movement is

jaw

not completed, 1-jaw stop

move

(objects are touched

ment

during opening), 2-jaws

signal

stop (objects are touched

s in

during closing), 3-jaws

place

stop (objects are not
touched at the specified
position);
Huiling, Tianji: 0 - the
movement is not
completed, 1 - the
movement is completed;
Dahuan: 0-The movement
is not completed, 1-The
gripper stops (the object is
not clamped), 2-The
gripper stops (the object is
clamped), 3-The object is
clamped and dropped

10 / 16

User Manual

Table 2-2 (continued)
Serial

Nam

Number

e

42

Exter

Variable Name

Data

Number

Detailed Description

Type

of Bytes

servo_id

uint8_t

1

Range[1~16]

servo_errcode

int32_t

4

It is consistent with the robot-

nal
servo
drive
ID
43

Exter
nal

driven fault code

servo
drive
fault
code
44

Exter

servo_state

int32_t

4

bit0:0 - servo not enabled, 1 -

nal

servo enabled bit1: 0 - servo

Servo

stopped, 1 - servo in operation

Drive

bit2: 0 - positive limit not

Status

triggered, 1 - positive limit

(485)

triggered bit3: 0 - negative
limit not triggered, 1 negative limit triggered bit4:
0 - positioning not completed,
1 - positioning completed
bit5: 0 - zero return is not
completed, 1 - zero return
completed

11 / 16

User Manual

Table 2-2 (continued)
Serial

Name

Number
45

Variable

Data Type

Name
The current

servo_ac

position of the

tual_pos

Number

Detailed Description

of Bytes
double

8

-

float

4

-

float

4

-

uint8_t

1

-

116

For details, see the

external servo
46

47

48

49

The current

servo_ac

speed of the

tual_spe

external servo

ed

The current

servo_ac

torque of the

tual_torq

external servo

ue

External Shaft

exaxis_o

(UDP) Out of

ut_slimit

Soft Limit Error

_error

External Axis

exaxis_s

See Table 2-

(UDP) status

tatus[4]

3 for details

structure definition,
which supports up to 4
axes

50

External Axis

exaxis_a

(UDP)

ctive_fla

activation flag

g

uint8_t

1

0 - inactive, 1 - activated

12 / 16

User Manual

Table 2-2 (continued)
Serial

Name

Variable Name

Number
51

External Axis

exaxis_motion_status

Data

Number

Detailed

Type

of Bytes

Description

uint8_t

1

0 - Finished, 1 - In

(UDP) motion

Motion, 2 -

state

Suspended, 3 Completed with
Stoppage

52

Analog input

cl_analog_input[2]

uint16_t

4

0-4095

tl_analog_input

uint16_t

2

0-4095

cl_analog_output[2]

uint16_t

4

0-4095

tl_analog_output

uint16_t

2

0-4095

gripper_fault_id

uint8_t

1

-

gripper_fault

uint16_t

2

-

gripper_active

uint16_t

2

-

gripper_position

uint8_t

1

-

gripper_speed

int8_t

1

-

to the control
box
53

End analog
input

54

Analog output
of the control
box

55

End analogue
output

56

Wrong
gripper
number

57

Gripper
malfunction

58

The gripper is
active

59

Gripper
position

60

Gripper speed

13 / 16

User Manual

Table 2-2 (continued)
Serial

Name

Variable Name

Number

Data

Number

Type

of Bytes

Detailed Description

61

Jaw current

gripper_current

int8_t

1

-

62

Gripper

gripper_temp

int

4

-

gripper_voltage

int

4

-

gripper_rotNum

float

4

-

gripper_rotSpeed

uint8_t

1

percentage

gripper_rotTorque

uint8_t

1

percentage

main_errcode

int

4

-

sub_errcode

int

4

-

welding_state

See

2

For details, see the

temperature
63

Gripper
voltage

64

The current
number of
turns of the
rotary jaw

65

The current
speed of
the rotating
gripper

66

The current
moment of
the rotating
jaw

67

Primary
fault code

68

Sub-fault
codes

69

Welding
status

70

SmartTool
state

Table2-4
smartToolState

int

definition of structure
4

-

14 / 16

User Manual

Table 2-2 (continued)
Serial

Name

Variable Name

Data

Number

Detailed

Type

of Bytes

Description

toolCoord[6]

double

48

-

wobjCoord[6]

double

48

-

exToolCoord[6]

double

48

-

exAxisCoord[6]

double

48

-

load

double

8

-

loadCog[3]

double

24

x、y、z

Number
71

Current tool
coordinate system
values

72

Current workpiece
coordinate system
values

73

Current external tool
coordinate system
values

74

Current extended axis
coordinate system
values

75

Current robot load
weight

76

Current robot load
center of mass

15 / 16

User Manual

2.2.2 Data Content - Structure Definition
(1) The external axis (UDP) state structure is defined in Table 2-3 below
Table 2-3 Definition of the external axis (UDP) state structure
data type

The name of the
variable

The meaning is explained in detail

double

exaxis_pos_back

External shaft position in mm

double

exaxis_speed_back

External shaft speed

int

exaxis_error_code

External shaft fault code

uint8_t

exaxis_rdy

Servo ready

uint8_t

exaxis_inpos

Servo in place

uint8_t

exaxis_alm

Servo alarm

uint8_t

exaxis_flerr

Follow the error

uint8_t

exaxis_nlimit

to the negative limit

uint8_t

exaxis_plimit

to the positive limit

uint8_t

exaxis_absofln

The driver 485 bus is disconnected
The communication timed out, and the

uint8_t

exaxis_oflin

communication between the control card and the
control box board 485 timed out

uint8_t

exaxis_home_status

The outer shaft is back to zero

(2) The welded structure is defined in Table 2-4 below
Table 2-4 Definition of the welded structure
Data type

The name of the
variable

The meaning is explained in detail
Weld Interruption Status:

uint8_t

breakOffState

0 - Welding is not interrupted
1- The welding has been interrupted
Welding arc status:

uint8_t

weldArcState

0 - The arc is not interrupted
1- The arc has been interrupted

16 / 16

User Manual

Appendix 1 Error Code Correspondence
When an alarm or fault occurs in the robot, the user can obtain the specific content
of the current robot error in the "error code" data of the status feedback, as shown below.
Appendix 1 Definition of robot error codes Fault Code Description
Appendix 1 Definition of error codes on port 8083
Fault codes

Illustrate

0

No faults

1

Drive failure

2

Failure to exceed the soft limit

3

Collision failures

4

Singular pose

5

Slave error

6

The command point is incorrect

7

IO error

8

Axle device error

9

File error

10

The parameter is incorrect

11

The extension shaft exceeds the soft limit error

12

Joint configuration warnings

