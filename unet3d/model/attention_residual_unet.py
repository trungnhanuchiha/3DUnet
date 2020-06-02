from functools import partial

from keras.layers import Input, LeakyReLU, Add, UpSampling3D, Activation, SpatialDropout3D, Conv3D, Concatenate, Softmax, Multiply
from keras.engine import Model
from .unet import create_convolution_block, concatenate
from keras.regularizers import l2


create_convolution_block = partial(create_convolution_block, activation=LeakyReLU, instance_normalization=True)


def attention_isensee2017_model(input_shape=(4, 128, 128, 128), n_base_filters=16, depth=5, dropout_rate=0.3,
                      n_segmentation_levels=3, n_labels=4, activation_name="sigmoid"):
    """
    This function builds a model proposed by Isensee et al. for the BRATS 2017 competition:
    https://www.cbica.upenn.edu/sbia/Spyridon.Bakas/MICCAI_BraTS/MICCAI_BraTS_2017_proceedings_shortPapers.pdf

    This network is highly similar to the model proposed by Kayalibay et al. "CNN-based Segmentation of Medical
    Imaging Data", 2017: https://arxiv.org/pdf/1701.03056.pdf


    :param input_shape:
    :param n_base_filters:
    :param depth:
    :param dropout_rate:
    :param n_segmentation_levels:
    :param n_labels:
    :param optimizer:
    :param initial_learning_rate:
    :param loss_function:
    :param activation_name:
    :return:
    """
    inputs = Input(input_shape)

    current_layer = inputs
    level_output_layers = list()
    level_filters = list()
    for level_number in range(depth):
        n_level_filters = (2**level_number) * n_base_filters
        level_filters.append(n_level_filters)

        if current_layer is inputs:
            in_conv = create_convolution_block(current_layer, n_level_filters)
        else:
            in_conv = create_convolution_block(current_layer, n_level_filters, strides=(2, 2, 2))

        context_output_layer = create_context_module(in_conv, n_level_filters, dropout_rate=dropout_rate)

        summation_layer = Add()([in_conv, context_output_layer])

        level_output_layers.append(summation_layer)
        current_layer = summation_layer

    segmentation_layers = list()
    for level_number in range(depth - 2, -1, -1):
        masked_current_layer = create_attention_gate(level_output_layers[level_number], current_layer, level_filters[level_number])
        up_sampling = create_up_sampling_module(current_layer, level_filters[level_number])
        concatenation_layer = concatenate([masked_current_layer, up_sampling], axis=1)
        localization_output = create_localization_module(concatenation_layer, level_filters[level_number])
        current_layer = localization_output
        if level_number < n_segmentation_levels:
            segmentation_layers.insert(0, Conv3D(n_labels, (1, 1, 1))(current_layer))

    output_layer = None
    for level_number in reversed(range(n_segmentation_levels)):
        segmentation_layer = segmentation_layers[level_number]
        if output_layer is None:
            output_layer = segmentation_layer
        else:
            output_layer = Add()([output_layer, segmentation_layer])

        if level_number > 0:
            output_layer = UpSampling3D(size=(2, 2, 2))(output_layer)

    activation_block = None

    if activation_name == 'sigmoid':
        activation_block = Activation(activation_name)(output_layer)
    elif activation_name == 'softmax':
        activation_block = Softmax(axis=1)(output_layer)

    model = Model(inputs=inputs, outputs=activation_block)
    return model


def create_conv3D(output_channels, kernel_size, use_bias=False):
    regularizer = l2(1e-5)
    conv3d = Conv3D(output_channels, kernel_size, kernel_regularizer=regularizer, 
                bias_regularizer=regularizer, use_bias=use_bias)
    return conv3d


def create_instance_norm(axis=1):
    regularizer = l2(1e-5)
    from keras_contrib.layers.normalization.instancenormalization import InstanceNormalization
    ins_norm = InstanceNormalization(axis=1, beta_regularizer=regularizer, gamma_regularizer=regularizer)
    return ins_norm


def create_attention_gate(x, gating, inter_channels):
    w_x = create_instance_norm(axis=1)(create_conv3D(inter_channels, (1, 1, 1)) (x))
    w_gating = create_instance_norm(axis=1)(create_conv3D(inter_channels, (1, 1, 1), use_bias=True)(gating))
    up_w_gating = UpSampling3D(size=(2, 2, 2))(w_gating)
    sum_x_gating = Add()([w_x, up_w_gating])
    relu_sum = Activation('relu')(sum_x_gating)
    psi = create_instance_norm(axis=1)(create_conv3D(1, (1, 1, 1), use_bias=True)(relu_sum))
    mask = Activation('sigmoid')(psi)
    masked_x = Multiply()([x, mask])
    return masked_x


def create_localization_module(input_layer, n_filters):
    convolution1 = create_convolution_block(input_layer, n_filters)
    convolution2 = create_convolution_block(convolution1, n_filters, kernel=(1, 1, 1))
    return convolution2


def create_up_sampling_module(input_layer, n_filters, size=(2, 2, 2)):
    up_sample = UpSampling3D(size=size)(input_layer)
    convolution = create_convolution_block(up_sample, n_filters)
    return convolution


def create_context_module(input_layer, n_level_filters, dropout_rate=0.3, data_format="channels_first"):
    convolution1 = create_convolution_block(input_layer=input_layer, n_filters=n_level_filters)
    dropout = SpatialDropout3D(rate=dropout_rate, data_format=data_format)(convolution1)
    convolution2 = create_convolution_block(input_layer=dropout, n_filters=n_level_filters)
    return convolution2



