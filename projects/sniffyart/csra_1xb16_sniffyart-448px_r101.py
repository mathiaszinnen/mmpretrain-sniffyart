_base_ = ['mmpretrain::csra/resnet101-csra_1xb16_voc07-448px.py']


SNIFFYART_CLASSES = [
    'cooking', 'dancing', 'drinking', 'eating', 'holding the nose', 'painting', 'peeing',
    'playing music', 'praying', 'reading', 'sleeping', 'smoking', 'sniffing', 'textile work',
    'writing', 'none'
]
#FRQS = [0.01773, 0.0257, 0.09156, 0.02482, 0.0452, 0.01108, 0.00222, 0.05185, 
        #0.03767, 0.0195, 0.00753, 0.08332, 0.01773, 0.01108, 0.0031, 0.5499]
FRQS = [56.41, 38.90345, 10.92159, 40.29286, 22.12157, 90.256, 451.28, 19.28547, 26.54588, 51.28182, 132.72941, 12.00213, 56.41, 90.256, 322.34286, 1.8185]
num_classes = len(SNIFFYART_CLASSES)

data_preprocessor = dict(
    num_classes = num_classes
)

train_dataloader = dict(
    dataset = dict(
        data_root='/hdd/datasets/sniffyart-extension/cls/VOC2012',
        split='train',
        classes=SNIFFYART_CLASSES
    )
)
val_dataloader = dict(
    dataset = dict(
        data_root='/hdd/datasets/sniffyart-extension/cls/VOC2012',
        split='val',
        classes=SNIFFYART_CLASSES
    )
)
test_dataloader = val_dataloader



model = dict(
    head = dict(
        num_classes=num_classes,
        loss=dict(pos_weight=FRQS, loss_weight=0.1)
    )
)

# train_dataloader = dict(
#     dataset = dict(
#         data_root='/hdd/datasets/sniffyart-extension/cls/VOC2012',
#         split='train',
#     )
# )
# val_dataloader = dict(
#     dataset = dict(
#         data_root='/hdd/datasets/sniffyart-extension/cls/VOC2012',
#         split='val',
#     )
# )
# test_dataloader = val_dataloader

#val_evaluator = [dict(type='ConfusionMatrix')]