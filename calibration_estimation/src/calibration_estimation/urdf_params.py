#!/usr/bin/env python

# Software License Agreement (BSD License)
#
# Copyright (c) 2008-2011, Willow Garage, Inc.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
#  * Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
#  * Redistributions in binary form must reproduce the above
#    copyright notice, this list of conditions and the following
#    disclaimer in the documentation and/or other materials provided
#    with the distribution.
#  * Neither the name of Willow Garage, Inc. nor the names of its
#    contributors may be used to endorse or promote products derived
#    from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

# Author: Michael Ferguson

import roslib; roslib.load_manifest('calibration_estimation')
import rospy

from urdf_python.urdf import *
import yaml

from calibration_estimation.joint_chain import JointChain
from calibration_estimation.tilting_laser import TiltingLaser
from calibration_estimation.camera import RectifiedCamera
from calibration_estimation.checkerboard import Checkerboard
from calibration_estimation.single_transform import SingleTransform, RPY_to_angle_axis

# Construct a dictionary of all the primitives of the specified type
def init_primitive_dict(start_index, config_dict, PrimitiveType):
    cur_index = start_index

    # Init the dictionary to store the constructed primitives
    primitive_dict = dict()

    # Process each primitive in the list of configs
    for key, elem in config_dict.items():
        primitive_dict[key] = PrimitiveType(elem)
        # Store lookup indices
        primitive_dict[key].start = cur_index
        primitive_dict[key].end = cur_index + primitive_dict[key].get_length()
        cur_index = primitive_dict[key].end

    # Return the primitives, as well as the end index
    return primitive_dict, cur_index

# Given a dictionary of initialized primitives, inflate
# their configuration, given a parameter vector
def inflate_primitive_dict(param_vec, primitive_dict):
    for key, elem in primitive_dict.items():
        elem.inflate(param_vec[elem.start:elem.end,0])

# Given a dictionary of initialized primitives, deflate
# their configuration, into the specified parameter vector
def deflate_primitive_dict(param_vec, primitive_dict):
    for key, elem in primitive_dict.items():
        param_vec[elem.start:elem.end,0] = elem.deflate()

# Iterate over config dictionary and determine which parameters should be free,
# based on the the flags in the free dictionary. Once computed, update the part
# of the target vector that corresponds to this primitive
def update_primitive_free(target_list, config_dict, free_dict):
    for key, elem in config_dict.items():
        if key in free_dict.keys():
            free = elem.calc_free(free_dict[key])
            target_list[elem.start:elem.end] = free

# Given a parameter vector, Compute the configuration of all the primitives of a given type
def primitive_params_to_config(param_vec, primitive_dict):
    config_dict = dict()
    for key, elem in primitive_dict.items():
        config_dict[key] = elem.params_to_config(param_vec[elem.start:elem.end,0])
    return config_dict


# Stores the current configuration of all the primitives in the system
class UrdfParams:
    def __init__(self, raw_urdf, config):
        urdf = URDF().parse(raw_urdf)
        self.configure(urdf, config)

    def configure(self, urdf, config_dict):
        self.urdf = urdf

        # set base_link to which all measurements are based
        try:
            self.base_link = config_dict['base_link']
        except:
            self.base_link = 'base_link'
        config_dict = config_dict['sensors']

        # length of parameter vector
        cur_index = 0

        # build our transforms
        self.transforms = dict()
        for joint_name in urdf.joints.keys():
            j = urdf.joints[joint_name]
            rot = j.origin.rotation
            rot = RPY_to_angle_axis(rot)
            p = j.origin.position + rot
            self.transforms[joint_name] = SingleTransform(joint_name, p)
            self.transforms[joint_name].start = cur_index
            self.transforms[joint_name].end = cur_index + self.transforms[joint_name].get_length()
            cur_index = self.transforms[joint_name].end

        # build our chains
        self.chains = dict()
        chain_dict = config_dict['chains']
        for chain_name in config_dict['chains']:            
            # create configuration
            this_config = config_dict['chains'][chain_name]
            this_config['joints'] = urdf.get_chain(this_config['root'], this_config['tip'], links=False)
            this_config['active_joints'] = list()
            this_config['axis'] = list()
            this_config['transforms'] = dict()
            this_config['gearing'] = config_dict['chains'][chain_name]['gearing']   # TODO: extract from URDF
            this_config['cov'] = config_dict['chains'][chain_name]['cov']
            for joint_name in this_config["joints"]:
                this_config['transforms'][joint_name] = self.transforms[joint_name]
                if urdf.joints[joint_name].joint_type == 'revolute':
                    this_config["active_joints"].append(joint_name)
                    axis = filter( lambda a: a!= " ", list(urdf.joints[joint_name].axis))
                    this_config["axis"].append( sum( [i[0]*int(i[1]) for i in zip([4,5,6], axis)] ) )
                elif urdf.joints[joint_name].joint_type == 'prismatic':
                    this_config["active_joints"].append(joint_name)
                    axis = filter( lambda a: a!= " ", list(urdf.joints[joint_name].axis))
                    this_config["axis"].append( sum( [i[0]*int(i[1]) for i in zip([1,2,3], axis)] ) )
                elif urdf.joints[joint_name].joint_type != 'fixed':
                    print 'Unknown joint type:', urdf.joints[joint_name].joint_type

            self.chains[chain_name] = JointChain(this_config)
            self.chains[chain_name].start = cur_index
            self.chains[chain_name].end = cur_index + self.chains[chain_name].get_length()
            cur_index = self.chains[chain_name].end

        self.tilting_lasers, cur_index = init_primitive_dict(cur_index, config_dict["tilting_lasers"], TiltingLaser)
        self.rectified_cams, cur_index = init_primitive_dict(cur_index, config_dict["rectified_cams"], RectifiedCamera)
        self.checkerboards,  cur_index = init_primitive_dict(cur_index, config_dict["checkerboards"],   Checkerboard)

        self.length = cur_index

    # Initilize free list
    def calc_free(self, free_dict):
        free_list = [0] * self.length
        update_primitive_free(free_list, self.transforms, free_dict["transforms"])
        update_primitive_free(free_list, self.chains, free_dict["chains"])
        update_primitive_free(free_list, self.tilting_lasers, free_dict["tilting_lasers"])
        update_primitive_free(free_list, self.rectified_cams, free_dict["rectified_cams"])
        update_primitive_free(free_list, self.checkerboards, free_dict["checkerboards"])
        return free_list

    def params_to_config(self, param_vec):
        assert(self.length == param_vec.size)
        config_dict = dict()
        config_dict["transforms"]     = primitive_params_to_config(param_vec, self.transforms)
        config_dict["chains"]         = primitive_params_to_config(param_vec, self.chains)
        config_dict["tilting_lasers"] = primitive_params_to_config(param_vec, self.tilting_lasers)
        config_dict["rectified_cams"] = primitive_params_to_config(param_vec, self.rectified_cams)
        config_dict["checkerboards"]  = primitive_params_to_config(param_vec, self.checkerboards)
        return config_dict

    def inflate(self, param_vec):
        assert(self.length == param_vec.size)
        inflate_primitive_dict(param_vec, self.transforms)
        for key, elem in self.chain.items():
            elem.inflate(param_vec[elem.start:elem.end,0])
            for joint_name in elem.joints:
                elem._transforms[joint_name] = self.transforms[joint_name]
        inflate_primitive_dict(param_vec, self.tilting_lasers)
        inflate_primitive_dict(param_vec, self.rectified_cams)
        inflate_primitive_dict(param_vec, self.checkerboards)

    def deflate(self):
        param_vec = numpy.matrix( numpy.zeros((self.length,1), float))
        deflate_primitive_dict(param_vec, self.transforms)
        deflate_primitive_dict(param_vec, self.chains)
        deflate_primitive_dict(param_vec, self.tilting_lasers)
        deflate_primitive_dict(param_vec, self.rectified_cams)
        deflate_primitive_dict(param_vec, self.checkerboards)
        return param_vec


if __name__ == '__main__':
    rospy.init_node('urdf_params')

    params = UrdfParams()
    urdf = URDF().load(rospy.get_param('~urdf_file'))
    config = yaml.load(open(rospy.get_param('~config_file')))
    params.configure(urdf, config)
