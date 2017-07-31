# wrap VipsOperation

from __future__ import division

import logging

from Vips import *

logger = logging.getLogger(__name__)

ffi.cdef('''
    typedef struct _VipsOperation {
        VipsObject parent_instance;

        // opaque
    } VipsOperation;

    VipsOperation* vips_operation_new (const char* name);

    typedef void *(*VipsArgumentMapFn) (VipsOperation* object, 
        GParamSpec* pspec,
        VipsArgumentClass* argument_class,
        VipsArgumentInstance* argument_instance, 
        void* a, void* b);

    void* vips_argument_map (VipsOperation* object,
        VipsArgumentMapFn fn, void* a, void* b);

    VipsOperation* vips_cache_operation_build (VipsOperation* operation);
    void vips_object_unref_outputs (VipsOperation *operation);

''')

# values for VipsArgumentFlags
_REQUIRED = 1
_CONSTRUCT = 2
_SET_ONCE = 4
_SET_ALWAYS = 8
_INPUT = 16
_OUTPUT = 32
_DEPRECATED = 64
_MODIFY = 128

# search an array with a predicate, recursing into subarrays as we see them
# used to find the match_image for an operation
def _find_inside(fn, array):
    for x in array:
        if fn(x):
            return x
        elif isinstance(x, list):
            result = _find_inside(fn, x)

            if result != None:
                return result

    return None

class Operation(VipsObject):

    def __init__(self, pointer):
        logger.debug('Operation.__init__: pointer = {0}'.format(pointer))
        super(Operation, self).__init__(pointer)

    def set(self, name, flags, match_image, value):
        # if the object wants an image and we have a constant, imageize it
        #
        # if the object wants an image array, imageize any constants in the
        # array
        if match_image:
            gtype = self.get_typeof(name)

            if gtype == GValue.image_type:
                value = package_index['Image'].imageize(match_image, value)
            elif gtype == GValue.array_image_type:
                value = [package_index['Image'].imageize(match_image, x) 
                         for x in value]

        # MODIFY args need to be copied before they are set
        if (flags & _MODIFY) != 0:
            logger.debug('copying MODIFY arg {0}'.format(name))
            # make sure we have a unique copy
            value = value.copy().copy_memory()

        super(Operation, self).set(name, value)

    # this is slow ... call as little as possible
    def getargs(self):
        args = []

        def add_construct(self, pspec, argument_class, argument_instance, a, b):
            flags = argument_class.flags
            if (flags & _CONSTRUCT) != 0:
                name = ffi.string(pspec.name)

                # libvips uses '-' to separate parts of arg names, but we
                # need '_' for Python
                name = name.replace('-', '_')

                args.append([name, flags])
            
            return ffi.NULL

        cb = ffi.callback('VipsArgumentMapFn', add_construct)
        vips_lib.vips_argument_map(self.pointer, cb, ffi.NULL, ffi.NULL)

        return args

    # string_options is any optional args coded as a string, perhaps
    # '[strip,tile=true]'
    @staticmethod
    def call(name, *args, **kwargs):
        logger.debug('VipsOperation.call: name = {0}, args = {1}, kwargs = {2}'.
            format(name, args, kwargs))

        # pull out the special string_options kwarg
        string_options = kwargs.pop('string_options', '')

        logger.debug('VipsOperation.call: string_options = {0}'.
            format(string_options))

        vop = vips_lib.vips_operation_new(name)
        if vop == ffi.NULL:
            raise Error('no such operation {0}'.format(name))
        op = Operation(vop)
        vop = None

        arguments = op.getargs()
        logger.debug('arguments = {0}'.format(arguments))

        # make a thing to quickly get flags from an arg name
        flags_from_name = {}
        for name, flags in arguments:
            flags_from_name[name] = flags

        # count required input args
        n_required = 0
        for name, flags in arguments:
            if ((flags & _INPUT) != 0 and 
                (flags & _REQUIRED) != 0 and 
                (flags & _DEPRECATED) == 0):
                n_required += 1

        if n_required != len(args):
            raise Error(('unable to call {0}: {1} arguments given, ' +
                         'but {2} required').format(name, len(args), n_required))

        # the first image argument is the thing we expand constants to
        # match ... look inside tables for images, since we may be passing
        # an array of image as a single param
        match_image = _find_inside(lambda x: 
                                   isinstance(x, package_index['Image']),
                                   args)
        logger.debug('VipsOperation.call: match_image = {0}'.format(match_image))

        # set any string options before any args so they can't be
        # overridden
        if not op.set_string(string_options):
            raise Error('unable to call {0}'.format(name))

        # set required and optional args
        n = 0
        for name, flags in arguments:
            if ((flags & _INPUT) != 0 and 
                (flags & _REQUIRED) != 0 and 
                (flags & _DEPRECATED) == 0):
                op.set(name, flags, match_image, args[n])
                n += 1

        for name, value in kwargs.items():
            op.set(name, flags_from_name[name], match_image, value)

        # build operation
        vop2 = vips_lib.vips_cache_operation_build(op.pointer)
        if vop2 == ffi.NULL:
            raise Error('unable to call {0}'.format(name))
        op2 = Operation(vop2)
        op = op2
        op2 = None
        vop2 = None

        result = []

        # fetch required output args, plus modified input images
        for name, flags in arguments:
            if ((flags & _OUTPUT) != 0 and 
                (flags & _REQUIRED) != 0 and 
                (flags & _DEPRECATED) == 0):
                result.append(op.get(name))

            if ((flags & _INPUT) != 0 and 
                (flags & _MODIFY) != 0):
                result.append(op.get(name))

        # fetch optional output args
        for name, value in kwargs.items():
            flags = flags_from_name[name]

            if ((flags & _OUTPUT) != 0 and 
                (flags & _REQUIRED) == 0 and 
                (flags & _DEPRECATED) == 0):
                result.append(op.get(name))

        vips_lib.vips_object_unref_outputs(op.pointer)

        if len(result) == 1:
            result = result[0]

        return result

__all__ = ['Operation']