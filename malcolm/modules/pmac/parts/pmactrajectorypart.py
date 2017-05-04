# Treat all division as float division even in python2
from __future__ import division

from collections import Counter

import numpy as np
from scanpointgenerator import CompoundGenerator

from malcolm.core import method_takes, REQUIRED, method_also_takes
from malcolm.modules.builtin.parts import StatefulChildPart
from malcolm.modules.builtin.vmetas import StringArrayMeta, NumberMeta
from malcolm.modules.pmac.infos import MotorInfo
from malcolm.modules.scanning.controllers import RunnableController
from malcolm.modules.scanning.infos import ParameterTweakInfo
from malcolm.modules.scanpointgenerator.vmetas import PointGeneratorMeta

# Number of seconds that a trajectory tick is
TICK_S = 0.000001

# Minimum move time for any move
MIN_TIME = 0.002

# Interval for interpolating velocity curves
INTERPOLATE_INTERVAL = 0.02

# velocity modes
PREV_TO_NEXT = 0
PREV_TO_CURRENT = 1
CURRENT_TO_NEXT = 2
ZERO_VELOCITY = 3

# user programs
NO_PROGRAM = 0       # Do nothing
TRIG_CAPTURE = 4     # Capture 1, Frame 0, Detector 0
TRIG_DEAD_FRAME = 2  # Capture 0, Frame 1, Detector 0
TRIG_LIVE_FRAME = 3  # Capture 0, Frame 1, Detector 1
TRIG_ZERO = 8        # Capture 0, Frame 0, Detector 0

# How many generator points to load each time
POINTS_PER_BUILD = 4000

# All possible PMAC CS axis assignment
cs_axis_names = list("ABCUVWXYZ")

# Args for configure and validate
configure_args = [
    "generator", PointGeneratorMeta("Generator instance"), REQUIRED,
    "axesToMove", StringArrayMeta(
        "List of axes in inner dimension of generator that should be moved"),
    []]


@method_also_takes(
    "minTurnaround", NumberMeta(
        "float64", "Min time for any gaps between frames"), 0.0)
class PmacTrajectoryPart(StatefulChildPart):
    # Axis information stored from validate
    # {scannable_name: MotorInfo}
    axis_mapping = None
    # Lookup of the completed_step value for each point
    completed_steps_lookup = []
    # If we are currently loading then block loading more points
    loading = False
    # The last index we have loaded
    end_index = 0
    # Where we should stop loading points
    steps_up_to = 0
    # Stored generator for positions
    generator = None
    # Min turnaround time
    min_turnaround = None

    def create_attributes(self):
        for data in super(PmacTrajectoryPart, self).create_attributes():
            yield data
        self.min_turnaround = NumberMeta(
            "float64", "Min time for any gaps between frames").create_attribute(
            self.params.minTurnaround)
        yield "minTurnaround", self.min_turnaround, \
              self.min_turnaround.set_value

    @RunnableController.Reset
    def reset(self, context):
        super(PmacTrajectoryPart, self).reset(context)
        self.abort(context)
        self.reset_triggers(context)

    @RunnableController.Validate
    @method_takes(*configure_args)
    def validate(self, context, part_info, params):
        self._make_axis_mapping(part_info, params.axesToMove)
        # Find the duration
        child = context.block_view(self.params.mri)
        assert params.generator.duration > 0, \
            "Can only do fixed duration at the moment"
        servo_freq = 8388608000. / child.i10.value
        # convert half an exposure to multiple of servo ticks, rounding down
        # + 0.002 for some observed jitter in the servo frequency (I18)
        ticks = np.floor(servo_freq * 0.5 * params.generator.duration) + 0.002
        # convert to integer number of microseconds, rounding up
        micros = np.ceil(ticks / servo_freq * 1e6)
        # back to duration
        duration = 2 * float(micros) / 1e6
        if duration != params.generator.duration:
            new_generator = CompoundGenerator(
                generators=params.generator.generators,
                excluders=params.generator.excluders,
                mutators=params.generator.mutators,
                duration=duration)
            return [ParameterTweakInfo("generator", new_generator)]

    def _make_axis_mapping(self, part_info, axes_to_move):
        cs_ports = set()
        # dict {name: MotorInfo}
        axis_mapping = {}
        for motor_info in MotorInfo.filter_values(part_info):
            if motor_info.scannable in axes_to_move:
                assert motor_info.cs_axis in cs_axis_names, \
                    "Can only scan 1-1 mappings, %r is %r" % \
                    (motor_info.scannable, motor_info.cs_axis)
                cs_ports.add(motor_info.cs_port)
                axis_mapping[motor_info.scannable] = motor_info
        missing = set(axes_to_move) - set(axis_mapping)
        assert not missing, \
            "Some scannables %s are not children of this controller" % missing
        assert len(cs_ports) == 1, \
            "Requested axes %s are in multiple CS numbers %s" % (
                axes_to_move, list(cs_ports))
        cs_axis_counts = Counter([x.cs_axis for x in axis_mapping.values()])
        # Any cs_axis defs that are used for more that one raw motor
        overlap = [k for k, v in cs_axis_counts.items() if v > 1]
        assert not overlap, \
            "CS axis defs %s have more that one raw motor attached" % overlap
        return cs_ports.pop(), axis_mapping

    @RunnableController.Configure
    @RunnableController.PostRunReady
    @RunnableController.Seek
    @method_takes(*configure_args)
    def configure(self, context, completed_steps, steps_to_do, part_info,
                  params):
        context.unsubscribe_all()
        child = context.block_view(self.params.mri)
        child.numPoints.put_value(4000000)
        self.generator = params.generator
        cs_port, self.axis_mapping = self._make_axis_mapping(
            part_info, params.axesToMove)
        # Set the right CS to move
        child.cs.put_value(cs_port)
        future = self.move_to_start(child, completed_steps)
        self.steps_up_to = completed_steps + steps_to_do
        self.completed_steps_lookup = []
        profile = self.build_generator_profile(completed_steps, do_run_up=True)
        context.wait_all_futures(future)
        self.write_profile_points(child, **profile)
        # Max size of array
        child.buildProfile()

    @RunnableController.Run
    @RunnableController.Resume
    def run(self, context, update_completed_steps):
        self.loading = False
        child = context.block_view(self.params.mri)
        child.pointsScanned.subscribe_value(
            self.update_step, update_completed_steps, child)
        child.executeProfile()

    @RunnableController.Abort
    def abort(self, context):
        child = context.block_view(self.params.mri)
        child.abortProfile()

    @RunnableController.Pause
    def pause(self, context):
        self.abort(context)
        context.sleep(0.5)
        self.reset_triggers(context)

    def update_step(self, scanned, update_completed_steps, child):
        if scanned > 0:
            completed_steps = self.completed_steps_lookup[scanned - 1]
            update_completed_steps(completed_steps, self)
            if not self.loading and self.end_index < self.steps_up_to and \
                    self.end_index - completed_steps < POINTS_PER_BUILD:
                self.loading = True
                profile = self.build_generator_profile(self.end_index)
                self.write_profile_points(child, **profile)
                child.appendProfile()
                self.loading = False

    def point_velocities(self, point):
        velocities = {}
        for axis_name, motor_info in self.axis_mapping.items():
            full_distance = point.upper[axis_name] - point.lower[axis_name]
            velocity = full_distance / point.duration
            assert abs(velocity) < motor_info.max_velocity, \
                "Velocity %s invalid for %r with max_velocity %s" % (
                    velocity, axis_name, motor_info.max_velocity)
            velocities[axis_name] = velocity
        return velocities

    def make_consistent_velocity_profiles(self, v1s, v2s, distances):
        time_arrays = {}
        velocity_arrays = {}
        iterations = 5
        min_time = MIN_TIME
        while iterations > 0:
            for axis_name, motor_info in self.axis_mapping.items():
                time_arrays[axis_name], velocity_arrays[axis_name] = \
                    motor_info.make_velocity_profile(
                        v1s[axis_name], v2s[axis_name], distances[axis_name],
                        min_time)
                assert time_arrays[axis_name][-1] >= min_time or np.isclose(
                        time_arrays[axis_name][-1], min_time), \
                    "Time %s velocity %s for %s takes less time than %s" % (
                        time_arrays[axis_name], velocity_arrays[axis_name],
                        axis_name, min_time)
            new_min_time = max(t[-1] for t in time_arrays.values())
            if np.isclose(new_min_time, min_time):
                # We've got our consistent set
                return time_arrays, velocity_arrays
            else:
                min_time = new_min_time
                iterations -= 1
        raise ValueError("Can't get a consistent time in 5 iterations")

    def move_to_start(self, child, start_index):
        """Move to the run up position ready to start the scan"""
        first_point = self.generator.get_point(start_index)
        zero_velocities = {}
        current_positions = {}
        distances = {}

        for axis_name, velocity in self.point_velocities(first_point).items():
            motor_info = self.axis_mapping[axis_name]
            acceleration_distance = motor_info.ramp_distance(0, velocity)
            zero_velocities[axis_name] = 0
            start_pos = first_point.lower[axis_name] - acceleration_distance
            current_positions[axis_name] = motor_info.current_position
            distances[axis_name] = start_pos - motor_info.current_position

        # Work out the velocity profiles of how to move to the start
        time_arrays, velocity_arrays = self.make_consistent_velocity_profiles(
            zero_velocities, zero_velocities, distances)

        # If the reported move is tiny, we don't have to try and move
        if max(ts[-1] for ts in time_arrays.values()) < 0.01:
            return []

        # Work out the Position trajectories from these velocity profiles
        profile = self.build_profile_from_velocities(
            time_arrays, velocity_arrays, current_positions)

        self.write_profile_points(child, **profile)
        child.buildProfile()
        future = child.executeProfile_async()
        return future

    def write_profile_points(self, child, time_array, velocity_mode, trajectory,
                             user_programs, completed_steps_lookup=None):
        """Build profile using part_contexts

        Args:
            time_array (list): List of times in ms
            velocity_mode (list): List of velocity modes like PREV_TO_NEXT
            trajectory (dict): {axis_name: [positions in EGUs]}
            child (Block): Child block for running
            user_programs (list): List of user programs like TRIG_LIVE_FRAME
            completed_steps_lookup (list): If given, when we get to this index,
                how many completed steps have we done?
        """
        # Work out which axes should be used and set their resolutions and
        # offsets
        use = []
        attr_dict = dict()
        for axis_name in trajectory:
            motor_info = self.axis_mapping[axis_name]
            cs_axis = motor_info.cs_axis
            use.append(cs_axis)
            attr_dict["resolution%s" % cs_axis] = motor_info.resolution
            attr_dict["offset%s" % cs_axis] = motor_info.offset
        for cs_axis in cs_axis_names:
            attr_dict["use%s" % cs_axis] = cs_axis in use
        child.put_attribute_values(attr_dict)

        # Start adding points, padding if the move time exceeds 4s
        i = 0
        while i < len(time_array):
            t = time_array[i]
            if t > 4:
                # split
                nsplit = int(t / 4.0 + 1)
                new_time_array = time_array[:i]
                new_velocity_mode = velocity_mode[:i]
                new_user_programs = user_programs[:i]
                if completed_steps_lookup is not None:
                    new_completed_steps_lookup = completed_steps_lookup[:i]
                for _ in range(nsplit):
                    new_time_array.append(t / nsplit)
                    new_velocity_mode.append(PREV_TO_NEXT)
                    new_user_programs.append(NO_PROGRAM)
                    if completed_steps_lookup is not None:
                        new_completed_steps_lookup.append(
                            new_completed_steps_lookup[-1])
                time_array = new_time_array + time_array[i+1:]
                user_programs = new_user_programs[:-1] + user_programs[i:]
                velocity_mode = new_velocity_mode[:-1] + velocity_mode[i:]
                if completed_steps_lookup is not None:
                    completed_steps_lookup = new_completed_steps_lookup[:-1] + \
                                             completed_steps_lookup[i:]

                for k, traj in trajectory.items():
                    new_traj = traj[:i]
                    per_section = float(traj[i] - traj[i-1]) / nsplit
                    for j in range(1, nsplit+1):
                        new_traj.append(traj[i-1] + j * per_section)
                    trajectory[k] = new_traj + traj[i+1:]

                i += nsplit
            else:
                i += 1

        # Process the time in ticks
        overflow = 0
        time_array_ticks = []
        for t in time_array:
            ticks = t / TICK_S
            overflow += (ticks % 1)
            ticks = int(ticks)
            if overflow > 0.5:
                overflow -= 1
                ticks += 1
            time_array_ticks.append(ticks)

        # Set the trajectories
        attr_dict = dict(
            timeArray=time_array_ticks,
            velocityMode=velocity_mode,
            userPrograms=user_programs,
            pointsToBuild=len(time_array)
        )
        for axis_name in trajectory:
            motor_info = self.axis_mapping[axis_name]
            cs_axis = motor_info.cs_axis
            attr_dict["positions%s" % cs_axis] = trajectory[axis_name]
        child.put_attribute_values(attr_dict)

        # Set completed_steps
        if completed_steps_lookup is not None:
            self.completed_steps_lookup += completed_steps_lookup

    def reset_triggers(self, context):
        """Just call a Move to the run up position ready to start the scan"""
        child = context.block_view(self.params.mri)
        child.numPoints.put_value(10)
        time_array = [0.1]
        velocity_mode = [ZERO_VELOCITY]
        user_programs = [TRIG_ZERO]
        trajectory = {}
        self.write_profile_points(child, time_array, velocity_mode, trajectory,
                                  user_programs)
        child.buildProfile()
        child.executeProfile()

    def build_profile_from_velocities(self, time_arrays, velocity_arrays,
                                      current_positions):
        trajectory = {}

        # Interpolate the velocity arrays at about 10ms
        move_time = max(t[-1] for t in time_arrays.values())
        # Make sure there are at least 2 of them
        num_intervals = max(int(np.floor(move_time / INTERPOLATE_INTERVAL)), 2)
        interval = move_time / num_intervals
        time_array = [interval] * num_intervals
        velocity_mode = [PREV_TO_NEXT] * (num_intervals - 1) + [CURRENT_TO_NEXT]
        user_programs = [TRIG_ZERO] * num_intervals

        # Do this for each velocity array
        for axis_name, motor_info in self.axis_mapping.items():
            trajectory[axis_name] = []
            ts = time_arrays[axis_name]
            vs = velocity_arrays[axis_name]
            position = current_positions[axis_name]
            for i in range(num_intervals):
                time = interval * (i + 1)
                # If we have exceeded the current segment, pop it and add it's
                # position in
                if time > ts[1] and not np.isclose(time, ts[1]):
                    position += motor_info.ramp_distance(
                        vs[0], vs[1], ts[1] - ts[0])
                    vs = vs[1:]
                    ts = ts[1:]
                assert len(ts) > 1, \
                   "Bad %s time %s velocity %s" % (time, ts, vs)
                fraction = (time - ts[0]) / (ts[1] - ts[0])
                velocity = fraction * (vs[1] - vs[0]) + vs[0]
                part_position = motor_info.ramp_distance(
                    vs[0], velocity, time - ts[0])
                trajectory[axis_name].append(position + part_position)

        profile = dict(time_array=time_array, velocity_mode=velocity_mode,
                       trajectory=trajectory, user_programs=user_programs)
        return profile

    def build_generator_profile(self, start_index, do_run_up=False):
        trajectory = {axis_name: [] for axis_name in self.axis_mapping}
        time_array = []
        velocity_mode = []
        user_programs = []
        completed_steps_lookup = []

        # Cap last point to steps_up_to
        self.end_index = start_index + POINTS_PER_BUILD
        if self.end_index > self.steps_up_to:
            self.end_index = self.steps_up_to

        # If we are doing the first build, do_run_up will be passed to flag
        # that we need a run up, else just continue from the previous point
        if do_run_up:
            point = self.generator.get_point(start_index)

            # Calculate how long to leave for the run-up (at least MIN_TIME)
            run_up_time = MIN_TIME
            for axis_name, velocity in self.point_velocities(point).items():
                trajectory[axis_name].append(point.lower[axis_name])
                motor_info = self.axis_mapping[axis_name]
                run_up_time = max(run_up_time,
                                  motor_info.acceleration_time(0, velocity))

            # Add lower bound
            time_array.append(run_up_time)
            velocity_mode.append(CURRENT_TO_NEXT)
            user_programs.append(TRIG_LIVE_FRAME)
            completed_steps_lookup.append(start_index)

        for i in range(start_index, self.end_index):
            point = self.generator.get_point(i)

            # Add position
            time_array.append(point.duration / 2.0)
            velocity_mode.append(PREV_TO_NEXT)
            user_programs.append(TRIG_CAPTURE)
            completed_steps_lookup.append(i)
            for axis_name, positions in trajectory.items():
                positions.append(point.positions[axis_name])

            # Add upper bound
            time_array.append(point.duration / 2.0)
            velocity_mode.append(PREV_TO_NEXT)
            user_programs.append(TRIG_LIVE_FRAME)
            completed_steps_lookup.append(i + 1)
            for axis_name, positions in trajectory.items():
                positions.append(point.upper[axis_name])

            # Check if we need to insert the upper bound point
            if i + 1 < self.steps_up_to:
                next_point = self.generator.get_point(i + 1)

                # Check if we need no insert a gap
                if not self.points_joined(point, next_point):
                    # Change last point to be dead frame
                    velocity_mode[-1] = PREV_TO_CURRENT
                    user_programs[-1] = TRIG_DEAD_FRAME

                    # Work out the start and end velocities for each axis
                    start_velocities = self.point_velocities(point)
                    end_velocities = self.point_velocities(next_point)
                    start_positions = {}
                    distances = {}
                    for axis_name, motor_info in self.axis_mapping.items():
                        start_positions[axis_name] = point.upper[axis_name]
                        distances[axis_name] = \
                            next_point.lower[axis_name] - point.upper[axis_name]

                    # Work out the velocity profiles of how to move to the start
                    time_arrays, velocity_arrays = \
                        self.make_consistent_velocity_profiles(
                            start_velocities, end_velocities, distances)

                    # Work out the Position trajectories from these profiles
                    profile = self.build_profile_from_velocities(
                        time_arrays, velocity_arrays, start_positions)

                    # Append them
                    time_array += profile["time_array"]
                    velocity_mode += profile["velocity_mode"]
                    user_programs += profile["user_programs"][:-1] + \
                                     [TRIG_LIVE_FRAME]
                    for axis_name in trajectory:
                        trajectory[axis_name] += \
                            profile["trajectory"][axis_name]
                    completed_steps_lookup += [i + 1] * len(
                        profile["time_array"])

        # Add the last tail off point
        if self.end_index == self.steps_up_to:
            point = self.generator.get_point(self.end_index - 1)

            # Change last point to be dead frame
            velocity_mode[-1] = PREV_TO_CURRENT
            user_programs[-1] = TRIG_DEAD_FRAME

            # Calculate how long to leave for the tail-off (at least MIN_TIME)
            tail_off_time = MIN_TIME
            for axis_name, velocity in self.point_velocities(point).items():
                motor_info = self.axis_mapping[axis_name]
                tail_off_time = max(tail_off_time,
                                    motor_info.acceleration_time(0, velocity))
                positions = trajectory[axis_name]
                tail_off = motor_info.ramp_distance(velocity, 0)
                positions.append(positions[-1] + tail_off)

            time_array.append(tail_off_time)
            velocity_mode.append(ZERO_VELOCITY)
            user_programs.append(TRIG_ZERO)
            completed_steps_lookup.append(self.end_index)

        profile = dict(time_array=time_array, velocity_mode=velocity_mode,
                       trajectory=trajectory, user_programs=user_programs,
                       completed_steps_lookup=completed_steps_lookup)
        return profile

    def points_joined(self, last_point, point):
        # Check for axes that need to move within the space between points
        for axis_name, motor_info in self.axis_mapping.items():
            if last_point.upper[axis_name] != point.lower[axis_name]:
                return False
        return True
