# 待写题清单（中文答案层）

筛选规则：未被图谱引用 / 未被现役题集占用 / >= 300 字符 / 两段答案中文占比 >= 0.2 / 同来源 / 原文间隔 >= 3

写题要求：题面像真实用户会问的问题、**中文提问**；不得出现与原文连续相同 12 字以上的片段；gradingNotes 用中文写两条要点，分别对应 A/B 两段。

## 人工判废的段（不再回收）

- `S14#s278`：导航目录（Home/Install/Contents/API Reference…）加章节标题清单，无正文内容
- `S21#s349`：资讯门户首页导航（Supplyframe/星计划/用户主页菜单/论坛入口）加文章标题列表，无正文
- `S21#s349~4`：新闻摘要卡片列表（街电拆解/算力进展/弹窗广告…），只有导语摘要
- `S21#s349~2`：新闻摘要卡片列表（MOSFET SOA/发改委汽车重组），只有导语摘要
- `S21#s349~5`：新闻摘要卡片列表，只有导语摘要

## 01. [dev] AI框架与推理 — 参考段 `S05#s97~2` + `S05#s109~2`

- 来源 S05；原文间隔 28；中文占比 A=0.521 B=0.567
- A 章节：端侧推理快速入门 > Linux篇 > 模型转换 > Netron可视化

```
mobilenetv2.ms模型的理解。

通过对模型的查看，可以知道mobilenetv2.ms模型定义了如下计算：
对格式为float32[1,224,224,3]的输入张量x进行不断卷积，最后通过MatMulFusion全连接层的矩阵乘法操作，并执行Softmax运算，得到1x1000的输出张量，该输出张量名为Default/head-MobileNetV2Head/Softmax-op204。

本例提供的mobilenetv2.ms模型为1000分类的图片分类模型，具体的分类类别本处不做叙述，但通过对模型的查看，可以知道该模型不包含对图片的前处理操作，接收1x224x224x3的float32数值，并得到1x1000的float32输出。
故在使用该模型进行推理时，用户需自行编码完成图片的前处理操作，将处理后的数据，传递给推理框架，进行前向推理，并对推理得到的1x1000的输出进行后处理。
```
- B 章节：端侧推理快速入门 > Windows篇 > 模型推理 > benchmark推理测试

```
通过inDataFile指定模型的输入数据文件input.bin。
在之前的Netron打开模型，我们已经知道mobilenetv2.ms模型接收float32的1x224x224x3张量。
benchmark的inDataFile选项默认接收二进制格式数据文件，input.bin文件按顺保存了150528个float32的二进制数值，跟模型要求的1x224x224x3数据量一致，且格式均为float32。

通常输入数据文件可以采用以下方式生成：

将模型训练数据集内的数据进行前处理，将前处理后的数据保存。

通过随机生成的方式，生成指定范围内的随机数据。

本例采用随机生成数作为输入，用户可以通过运行以下Python脚本，或点击此处下载本例中的input.bin文件，并将它放到benchmark目录内。

import numpy as np
np.random.seed(1)
t = np.random.rand(1,224,224,3)
t.astype(np.float32).tofile("input.bin")

在提供了输入数据之后，用户还需提供用于跟推理结果进行对比的标杆数据，以进行精度误差分析。
本例通过benchmarkDataFile指定模型的输出标杆文件output.txt。标杆文件的格式需如下所示：

[输出节点1名称] [输出节点1形状的维度长度] [输出节点1形状的第一维值] ... [输出节点1形状的第n维值]
[节点1数据1] [节点1数据2] ...
[输出节点2名称] [输出节点2形状的维度长度] [输出节点2形状的第一维值] ... [输出节点2形状的第n维值]
[节点2数据1] [节点2数据2] ...

通常标杆文件可以采用以下方式生成：

跟其他框架进行对比：使用其它深度学习模型推理框架，并使用相同输入，将推理结果按照上面的要求格式保存。

跟模型训练时进行对比：在训练框架中，将经过前处理后的数据保存作为inDataFile指定的输入数据。并将模型推理后，还未经过后处理的输出数据按标杆格式进行保存，作为标杆。

跟不同设备…
```

## 02. [dev] AI框架与推理 — 参考段 `S05#s95` + `S05#s105`

- 来源 S05；原文间隔 27；中文占比 A=0.303 B=0.3
- A 章节：端侧推理快速入门 > Linux篇 > 模型转换 > 下载发布件

```
用户可在MindSpore Lite官网下载页面，得到各版本的MindSpore Lite发布件。
在本例中，我们选择的是软件系统为Linux、底层架构为x86_64的CPU发布件，以1.6.0版本为例，用户可点击此处直接下载。
每个发布件的包结构会有不同，本例中，Linux发布件的包结构如下（随着用户对MindSpore Lite各个功能的学习，我们将逐步讲解发布件中文件的作用，在此处，用户对发布件结构有个简略印象即可）：

mindspore-lite-{version}-linux-x64
├── runtime
│ ├── include # MindSpore Lite集成开发的API头文件
│ ├── lib
│ │ ├── libmindspore-lite.a # MindSpore Lite推理框架的静态库
│ │ ├── libmindspore-lite-jni.so # MindSpore Lite推理框架的JNI动态库
│ │ ├── libmindspore-lite.so # MindSpore Lite推理框架的动态库
│ │ ├── libmindspore-lite-train.a # MindSpore Lite训练框架的静态库
│ │ ├── libmindspore-lite-train.so # MindSpore Lite训练框架的动态库
│ │ └── mindspore-lite-java.jar # MindSpore Lite推理框架jar包
│ └── third_party
│ └── libjpeg-turbo
└── tools
├── benchmark # 基准测试工具目录
├── benchmark_train # 训练模型基准测试工具目录
├── codegen # 代码生成工具目录
├── converter # 模型转换工具目录
└── cropper # 库裁剪工具目录
```
- B 章节：端侧推理快速入门 > Windows篇 > 模型转换 > 转换模型

```
解压刚刚下载的发布件，在位于mindspore-lite-{version}-win-x64\tools\converter\converter目录，可以找到converter_lite.exe工具。
converter_lite.exe模型转换工具提供了离线转换模型功能，支持MindSpore、CAFFE、TensorFlow Lite、TensorFlow、ONNX类型的模型转换。
模型转换步骤如下：

环境设置

将转换工具需要的动态链接库加入环境变量PATH。

set PATH=%PACKAGE_ROOT_PATH%\tools\converter\lib;%PATH%

进入转换工具所在目录

执行如下命令，进入转换工具所在目录。

cd %PACKAGE_ROOT_PATH%\tools\converter\converter

转换参数说明

在使用converter_lite.exe执行转换时，需设定相关参数。本例中用到的相关参数定义如下表3所示。

下面以各种类型模型的转换命令为例，说明各参数的使用方法。

以Caffe模型lenet.prototxt为例，执行转换命令。

call converter_lite.exe --fmk=CAFFE --modelFile=lenet.prototxt --weightFile=lenet.caffemodel --outputFile=lenet

在转换Caffe模型时，将fmk配置为CAFFE（--fmk=CAFFE），同时分别通过modelFile和weightFile参数传入模型结构（lenet.prototxt）、模型权值（lenet.caffemodel）两个文件。
同时通过outputFile，指定转换后输出的模型名，因未指定路径，生成的模型默认在当前路径，并带有.ms后缀，即lenet.ms。

以MindSpore、TensorFlow Lite、TensorFlow和ONNX模型为例，执行转换命令。

MindSpore模型lenet.mindir。

call converter_…
```

## 03. [dev] AI框架与推理 — 参考段 `S05#s96~2` + `S05#s105~2`

- 来源 S05；原文间隔 26；中文占比 A=0.423 B=0.416
- A 章节：端侧推理快速入门 > Linux篇 > 模型转换 > 转换模型

```
./converter_lite --fmk=MINDIR --modelFile=mobilenetv2.mindir --outputFile=mobilenetv2

执行命令后，若转换成功，结果显示如下，并在当前目录生成名为mobilenetv2.ms的新模型文件。

CONVERT RESULT SUCCESS:0

高级功能

关于转换工具的更详细说明，可参考端侧模型转换。

关于如何使用转换工具实现模型训练后量化，可参考量化。

如果用户希望转换后的模型能进行训练，需进行训练模型转换，详细可参考端侧训练模型转换。

表1：converter_lite参数定义

参数

是否必选

参数说明

取值范围

--fmk=<FMK>

待转换模型的原始格式。

MINDIR、CAFFE、TFLITE、TF、ONNX

--modelFile=<MODELFILE>

待转换模型的路径。

--outputFile=<OUTPUTFILE>

转换后模型的路径及模型名，不需加后缀，可自动生成.ms后缀。

--weightFile=<WEIGHTFILE>

转换Caffe模型时必选

输入模型weight文件的路径。

参数名和参数值之间用等号连接，中间不能有空格。

Caffe模型一般分为两个文件：*.prototxt模型结构，对应--modelFile参数；*.caffemodel模型权值，对应--weightFile参数。
```
- B 章节：端侧推理快速入门 > Windows篇 > 模型转换 > 转换模型

```
call converter_lite.exe --fmk=MINDIR --modelFile=mobilenetv2.mindir --outputFile=mobilenetv2

执行命令后，若转换成功，结果显示如下，并在当前目录生成名为mobilenetv2.ms的新模型文件。

CONVERT RESULT SUCCESS:0

高级功能

关于转换工具的更详细说明，可参考端侧模型转换。

关于如何使用转换工具实现模型训练后量化，可参考量化。

如果用户希望转换后的模型能进行训练，需进行训练模型转换，详细可参考端侧训练模型转换。

表3：converter_lite.exe参数定义

参数

是否必选

参数说明

取值范围

--fmk=<FMK>

待转换模型的原始格式。

MINDIR、CAFFE、TFLITE、TF、ONNX

--modelFile=<MODELFILE>

待转换模型的路径。

--outputFile=<OUTPUTFILE>

转换后模型的路径及模型名，不需加后缀，可自动生成.ms后缀。

--weightFile=<WEIGHTFILE>

转换Caffe模型时必选

输入模型weight文件的路径。

参数名和参数值之间用等号连接，中间不能有空格。

Caffe模型一般分为两个文件：*.prototxt模型结构，对应--modelFile参数；*.caffemodel模型权值，对应--weightFile参数。
```

## 04. [dev] AI框架与推理 — 参考段 `S05#s101~5` + `S05#s110~5`

- 来源 S05；原文间隔 24；中文占比 A=0.345 B=0.319
- A 章节：端侧推理快速入门 > Linux篇 > 模型推理 > 集成推理

```
首先创建一个Model类对象model，Model类定义了MindSpore Lite中的模型，用于计算图管理。
关于Model类的详细说明，可参考API文档。

auto model = new (std::nothrow) mindspore::Model();

接着调用Build接口传入模型，将模型编译至可在设备上运行的状态。
在加载编译完模型之后，被解析的模型信息已记录在model变量中，原先的模型文件内存model_buf可以释放。
由于model_buf是以char数组的方式申请的，故使用delete[]释放内存。

auto build_ret = model->Build(model_buf, size, mindspore::kMindIR, context);
delete[](model_buf);

(4) 传入数据

在执行模型推理前，需要设置推理的输入数据。
此例，通过Model.GetInputs接口，获取模型的所有输入张量。单个张量的格式为MSTensor。
关于MSTensor张量的详细说明，请参考MSTensor的API说明。

auto inputs = model->GetInputs();

通过张量的MutableData接口，可以获取张量的数据内存指针。
在本例中，模型的输入为浮点数格式，所以此处将指针强转为浮点指针。用户可根据自己模型的数据格式做不同处理，也可通过张量的DataType接口，得到该张量的数据类型。

auto input_data = reinterpret_cast<float *>(tensor.MutableData());

接着，通过数据指针，将我们要推理的数据传入张量内部。
在本例中我们传入的是随机生成的0.1至1的浮点数据，且数据呈平均分布。
在实际的推理中，用户在读取图片或音频等实际数据后，需进行算法特定的预处理操作，并将处理后的数据传入模型。

template <typename T, typename Distribution>
void GenerateRandomData(int…
```
- B 章节：端侧推理快速入门 > Windows篇 > 模型推理 > 集成推理

```
接着，通过Context::MutableDeviceInfo接口，得到context对象的设备管理列表。

auto &device_list = context->MutableDeviceInfo();

在本例中，由于使用CPU进行推理，故需申请一个CPUDeviceInfo类的对象device_info。

auto device_info = std::make_shared<mindspore::CPUDeviceInfo>();

因为采用了CPU的默认设置，所以不需对device_info对象做任何设置，直接添加到context的设备管理列表。

device_list.push_back(device_info);

(3) 加载模型

首先创建一个Model类对象model，Model类定义了MindSpore Lite中的模型，用于计算图管理。
关于Model类的详细说明，可参考API文档。

auto model = new (std::nothrow) mindspore::Model();

接着调用Build接口传入模型，将模型编译至可在设备上运行的状态。
在加载编译完模型之后，被解析的模型信息已记录在model变量中，原先的模型文件内存model_buf可以释放。
由于model_buf是以char数组的方式申请的，故使用delete[]释放内存。

auto build_ret = model->Build(model_buf, size, mindspore::kMindIR, context);
delete[](model_buf);

(4) 传入数据

在执行模型推理前，需要设置推理的输入数据。
此例，通过Model.GetInputs接口，获取模型的所有输入张量。单个张量的格式为MSTensor。
关于MSTensor张量的详细说明，请参考MSTensor的API说明。

auto inputs = model->GetInputs();

通过张量的MutableData接口，可以获取张量的数据内存指针。
在本例中，模型…
```

## 05. [dev] AI框架与推理 — 参考段 `S05#s104` + `S05#s110~6`

- 来源 S05；原文间隔 14；中文占比 A=0.271 B=0.379
- A 章节：端侧推理快速入门 > Windows篇 > 模型转换 > 下载发布件

```
用户可在MindSpore Lite官网下载页面，得到各版本的MindSpore Lite发布件。
在本例中，我们选择的是软件系统为Windows、底层架构为x86_64的CPU发布件，以1.6.0版本为例，用户可点击此处直接下载。
每个发布件的包结构会有不同。本例中，Windows发布件的包结构如下：

mindspore-lite-{version}-win-x64
├── runtime
│ ├── include
│ └── lib
│ ├── libgcc_s_seh-1.dll # MinGW动态库
│ ├── libmindspore-lite.a # MindSpore Lite推理框架的静态库
│ ├── libmindspore-lite.dll # MindSpore Lite推理框架的动态库
│ ├── libmindspore-lite.dll.a # MindSpore Lite推理框架的动态库的链接文件
│ ├── libssp-0.dll # MinGW动态库
│ ├── libstdc++-6.dll # MinGW动态库
│ └── libwinpthread-1.dll # MinGW动态库
└── tools
├── benchmark # 基准测试工具目录
└── converter # 模型转换工具目录
```
- B 章节：端侧推理快速入门 > Windows篇 > 模型推理 > 集成推理

```
GenerateRandomData<float>(tensor.DataSize(), input_data, std::uniform_real_distribution<float>(0.1f, 1.0f));

(5) 执行推理

首先申请一个放置模型推理输出张量的数组outputs，然后调用模型推理接口Predict，将输入张量和输出张量作为参数。
在推理成功后，输出张量被保存在outputs内。

std::vector<MSTensor> outputs;
auto status = model->Predict(inputs, &outputs);

(6) 推理结果核验

通过MutableData得到输出张量的数据指针。
本例中，将它强转为浮点指针，用户可以根据自己模型的数据类型进行对应类型的转换，也可通过张量的DataType接口得到数据类型。

auto out_data = reinterpret_cast<float *>(tensor.MutableData());

在本例中，直接通过打印来观察推理输出结果的准确性。

for (int i = 0; i < tensor.ElementNum(); i++) {
std::cout << out_data[i] << " ";
}

(7) 释放model对象

delete model;

编译

进入build目录，输入cmake -G "CodeBlocks - MinGW Makefiles" ..生成makefile文件，然后输入cmake --build .编译工程。在编译成功后，可以在build目录下得到demo可执行程序。

运行推理程序

将libmindspore-lite.so动态库的地址加入环境变量PATH。

set PATH=..\runtime\lib;%PATH%

输入call demo执行demo程序，根据上文，我们知道demo程序将加载mobilenetv2.ms模型，并将随机生成的输入张量传递给模型进行推理，将推理后的输出张量的值进行打印。
推理成…
```

## 06. [dev] AI框架与推理 — 参考段 `S07#s125` + `S07#3.5`

- 来源 S07；原文间隔 14；中文占比 A=0.635 B=0.228
- A 章节：Tensor 介绍 > 二、Tensor 的创建

```
飞桨可基于给定数据手动创建 Tensor，并提供了多种方式，如：

2.1 指定数据创建

2.2 指定形状创建

2.3 指定区间创建

另外在常见深度学习任务中，数据样本可能是图片（image）、文本（text）、语音（audio）等多种类型，在送入神经网络训练或推理前均需要创建为 Tensor。飞桨提供了将这类数据手动创建为 Tensor 的方法，如：

2.4 指定图像、文本数据创建

由于这些操作在整个深度学习任务流程中比较常见且固定，飞桨在一些 API 中封装了 Tensor 自动创建的操作，从而无须手动转 Tensor。

2.5 自动创建 Tensor 的功能介绍

如果你熟悉 Numpy，已经使用 Numpy 数组创建好数据，飞桨可以很方便地将 Numpy 数组转为 Tensor，具体介绍如：

六、Tensor 与 Numpy 数组相互转换
```
- B 章节：Tensor 介绍 > 三、Tensor 的属性 > 3.5

```
stop_gradient 表示是否停止计算梯度，默认值为 True，表示停止计算梯度，梯度不再回传。在设计网络时，如不需要对某些参数进行训练更新，可以将参数的 stop_gradient 设置为 True。可参考以下代码直接设置 stop_gradient 的值。

eg = paddle.to_tensor(1)
print("Tensor stop_gradient:", eg.stop_gradient)
eg.stop_gradient = False
print("Tensor stop_gradient:", eg.stop_gradient)

Tensor stop_gradient: True
Tensor stop_gradient: False
```

## 07. [dev] AI框架与推理 — 参考段 `S07#2.4` + `S07#4.2`

- 来源 S07；原文间隔 12；中文占比 A=0.265 B=0.436
- A 章节：Tensor 介绍 > 二、Tensor 的创建 > 2.4

```
在常见深度学习任务中，数据样本可能是图片（image）、文本（text）、语音（audio）等多种类型，在送入神经网络训练或推理前，这些数据和对应的标签均需要创建为 Tensor。以下是图像场景和 NLP 场景中手动转换 Tensor 方法的介绍。

对于图像场景，可使用 paddle.vision.transforms.ToTensor 直接将 PIL.Image 格式的数据转为 Tensor，使用 paddle.to_tensor 将图像的标签（Label，通常是 Python 或 Numpy 格式的数据）转为 Tensor。

对于文本场景，需将文本数据解码为数字后，再通过 paddle.to_tensor 转为 Tensor。不同文本任务标签形式不一样，有的任务标签也是文本，有的则是数字，均需最终通过 paddle.to_tensor 转为 Tensor。

下面以图像场景为例介绍，以下示例代码中将随机生成的图片转换为 Tensor。

import numpy as np
from PIL import Image
import paddle.vision.transforms as T
import paddle.vision.transforms.functional as F

fake_img = Image.fromarray((np.random.rand(224, 224, 3) * 255.).astype(np.uint8)) # 创建随机图片
transform = T.ToTensor()
tensor = transform(fake_img) # 使用 ToTensor()将图片转换为 Tensor
print(tensor)

Tensor(shape=[3, 224, 224], dtype=float32, place=Place(gpu:0), stop_gradient=True,
[[[0.78039223, 0.72941178, 0.34117648, ..., 0.76470596, 0.57647061, 0.94…
```
- B 章节：Tensor 介绍 > 四、Tensor 的操作 > 4.2

```
x.abs() #逐元素取绝对值
x.ceil() #逐元素向上取整
x.floor() #逐元素向下取整
x.round() #逐元素四舍五入
x.exp() #逐元素计算自然常数为底的指数
x.log() #逐元素计算 x 的自然对数
x.reciprocal() #逐元素求倒数
x.square() #逐元素计算平方
x.sqrt() #逐元素计算平方根
x.sin() #逐元素计算正弦
x.cos() #逐元素计算余弦
x.add(y) #逐元素相加
x.subtract(y) #逐元素相减
x.multiply(y) #逐元素相乘
x.divide(y) #逐元素相除
x.mod(y) #逐元素相除并取余
x.pow(y) #逐元素幂运算
x.max() #指定维度上元素最大值，默认为全部维度
x.min() #指定维度上元素最小值，默认为全部维度
x.prod() #指定维度上元素累乘，默认为全部维度
x.sum() #指定维度上元素的和，默认为全部维度

飞桨框架对 Python 数学运算相关的魔法函数进行了重写，例如：

x + y -> x.add(y) #逐元素相加
x - y -> x.subtract(y) #逐元素相减
x * y -> x.multiply(y) #逐元素相乘
x / y -> x.divide(y) #逐元素相除
x % y -> x.mod(y) #逐元素相除并取余
x ** y -> x.pow(y) #逐元素幂运算
```

## 08. [dev] AI框架与推理 — 参考段 `S07#2.5` + `S07#4.3`

- 来源 S07；原文间隔 12；中文占比 A=0.264 B=0.391
- A 章节：Tensor 介绍 > 二、Tensor 的创建 > 2.5

```
除了手动创建 Tensor 外，实际在飞桨框架中有一些 API 封装了 Tensor 创建的操作，从而无需用户手动创建 Tensor。例如 paddle.io.DataLoader 能够基于原始 Dataset，返回读取 Dataset 数据的迭代器，迭代器返回的数据中的每个元素都是一个 Tensor。另外在一些高层 API，如 paddle.Model.fit 、paddle.Model.predict ，如果传入的数据不是 Tensor，会自动转为 Tensor 再进行模型训练或推理。

说明：

paddle.Model.fit、paddle.Model.predict 等高层 API 支持传入 Dataset 或 DataLoader，如果传入的是 Dataset，那么会用 DataLoader 封装转为 Tensor 数据；如果传入的是 DataLoader，则直接从 DataLoader 迭代读取 Tensor 数据送入模型训练或推理。因此即使没有写将数据转为 Tensor 的代码，也能正常执行，提升了编程效率和容错性。

以下示例代码中，分别打印了原始数据集的数据，和送入 DataLoader 后返回的数据，可以看到数据结构由 Python list 转为了 Tensor。

import paddle

from paddle.vision.transforms import Compose, Normalize

transform = Compose([Normalize(mean=[127.5],
std=[127.5],
data_format='CHW')])

test_dataset = paddle.vision.datasets.MNIST(mode='test', transform=transform)
print(test_dataset[0][1]) # 打印原始数据集的第一个数据的 label
loader = paddle.io.DataLoader(test_dataset)
for data in enumerate(l…
```
- B 章节：Tensor 介绍 > 四、Tensor 的操作 > 4.3

```
x.isfinite() #判断 Tensor 中元素是否是有限的数字，即不包括 inf 与 nan
x.equal_all(y) #判断两个 Tensor 的全部元素是否相等，并返回形状为[]的布尔类 0-D Tensor
x.equal(y) #判断两个 Tensor 的每个元素是否相等，并返回形状相同的布尔类 Tensor
x.not_equal(y) #判断两个 Tensor 的每个元素是否不相等
x.less_than(y) #判断 Tensor x 的元素是否小于 Tensor y 的对应元素
x.less_equal(y) #判断 Tensor x 的元素是否小于或等于 Tensor y 的对应元素
x.greater_than(y) #判断 Tensor x 的元素是否大于 Tensor y 的对应元素
x.greater_equal(y) #判断 Tensor x 的元素是否大于或等于 Tensor y 的对应元素
x.allclose(y) #判断 Tensor x 的全部元素是否与 Tensor y 的全部元素接近，并返回形状为[]的布尔类 0-D Tensor

同样地，飞桨框架对 Python 逻辑比较相关的魔法函数进行了重写，以下操作与上述结果相同。

x == y -> x.equal(y) #判断两个 Tensor 的每个元素是否相等
x != y -> x.not_equal(y) #判断两个 Tensor 的每个元素是否不相等
x < y -> x.less_than(y) #判断 Tensor x 的元素是否小于 Tensor y 的对应元素
x <= y -> x.less_equal(y) #判断 Tensor x 的元素是否小于或等于 Tensor y 的对应元素
x > y -> x.greater_than(y) #判断 Tensor x 的元素是否大于 Tensor y 的对应元素
x >= y -> x.greater_equal(y) #判断 Tensor x 的元素是否大于或等于 Tensor y 的对应元素

以…
```

## 09. [test] AI框架与推理 — 参考段 `S07#2.3` + `S07#4.1`

- 来源 S07；原文间隔 12；中文占比 A=0.272 B=0.219
- A 章节：Tensor 介绍 > 二、Tensor 的创建 > 2.3

```
如果要在指定区间内创建 Tensor，可以使用paddle.arange、 paddle.linspace 实现。

paddle.arange(start, end, step) # 创建以步长 step 均匀分隔区间[start, end)的 Tensor
paddle.linspace(start, stop, num) # 创建以元素个数 num 均匀分隔区间[start, stop)的 Tensor

示例如下：

paddle.arange(start=1, end=5, step=1)

Tensor(shape=[4], dtype=int64, place=Place(gpu:0), stop_gradient=True,
[1, 2, 3, 4])

说明：

除了以上指定数据、形状、区间创建 Tensor 的方法，飞桨还支持如下类似的创建方式，如：

创建一个空 Tensor，即根据 shape 和 dtype 创建尚未初始化元素值的 Tensor，可通过 paddle.empty 实现。

创建一个与其他 Tensor 具有相同 shape 与 dtype 的 Tensor，可通过 paddle.ones_like 、 paddle.zeros_like 、 paddle.full_like 、paddle.empty_like 实现。

拷贝并创建一个与其他 Tensor 完全相同的 Tensor，可通过 paddle.clone 实现。

创建一个满足特定分布的 Tensor，如 paddle.rand, paddle.randn , paddle.randint 等。

通过设置随机种子创建 Tensor，可每次生成相同元素值的随机数 Tensor，可通过 paddle.seed 和 paddle.rand 组合实现。
```
- B 章节：Tensor 介绍 > 四、Tensor 的操作 > 4.1

```
通过索引可访问或修改 Tensor。飞桨框架支持标准的 Python 索引规则（基础索引），与 Indexing a list or a string in Python 类似，并与 Numpy 等其他框架一样，支持更灵活的索引方式（高级索引、联合索引）。在飞桨中，索引操作均支持在 GPU 等其他设备上运行以实现计算加速，并均支持自动反向梯度计算。

场景

取值(__getitem__)

赋值(__setitem__)

基础索引

· y = x[0, 2:4]

等价于:

y = paddle.slice(x, [0,1], [0,2], [1,4], decrease_axes=[1])

· x[0, 2:3] = Tensor(1.0)

等价于：

paddle.slice_scatter_(x, [0,1], [0,2], [1,4], decrease_axes=[1])

高级索引

· y = x[[0,1], [2,3]]

等价于：

index = paddle.stack([Tensor([0,1]), Tensor([2,3]), axis=1) y = paddle.gather_nd(x, index)

· x[[0,1], [2,3]] = Tensor(1.0)

等价于：

paddle.index_put_(x, ([Tensor([0,1]), Tensor([2,3]), Tensor(1.0))

联合索引

· y = x[0, [0,2], ..., 2:5:2, None]

等价替换超过 10 行代码

· x[0, [0,2], ..., 2:5:2, None] = 1.0

等价替换超过 10 行代码

关于索引规则的详细介绍，请参考Paddle Tensor 索引介绍

同时，飞桨还提供了丰富的 Tensor 操作的 API，包括数学运算、逻辑运算、线性代数等 100 余种 API，这些 API 调用有两种方法：

x = paddle.to_tensor([[1.1, 2.2], [3.3, 4.…
```

## 10. [test] AI框架与推理 — 参考段 `S07#3.1~2` + `S07#s141`

- 来源 S07；原文间隔 11；中文占比 A=0.372 B=0.479
- A 章节：Tensor 介绍 > 三、Tensor 的属性 > 3.1

```
0 表示该维度的元素数量与原值相同，因此 shape 中 0 的索引值必须小于 Tensor 的维度（索引值从 0 开始计，如第 1 维的索引值是 0，第二维的索引值是 1）。

通过几个例子来详细了解：

origin:[3, 2, 5] reshape:[3, 10] actual: [3, 10] # 直接指定目标 shape
origin:[3, 2, 5] reshape:[-1] actual: [30] # 转换为 1 维，维度根据元素总数推断出来是 3*2*5=30
origin:[3, 2, 5] reshape:[-1, 5] actual: [6, 5] # 转换为 2 维，固定一个维度 5，另一个维度根据元素总数推断出来是 30÷5=6
origin:[3, 2, 5] reshape:[0, -1] actual: [3, 10] # reshape:[0, -1]中 0 的索引值为 0，按照规则，转换后第 0 维的元素数量与原始 Tensor 第 0 维的元素数量相同，为 3；第 1 维的元素数量根据元素总值计算得出为 30÷3=10。
origin:[3, 2] reshape:[3, 1, 0] error： # reshape:[3, 1, 0]中 0 的索引值为 2，但原 Tensor 只有 2 维，无法找到与第 3 维对应的元素数量，因此出错。

从上面的例子可以看到，通过 reshape:[-1] ，可以很方便地将 Tensor 按其在计算机上的内存分布展平为一维。

print("Tensor flattened to Vector:", paddle.reshape(ndim_3_Tensor, [-1]).numpy())

Tensor flattened to Vector: [1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30]

说明：

除了 paddle.reshape 可重置 Tensor 的形状，还可…
```
- B 章节：Tensor 介绍 > 五、Tensor 的广播机制

```
在深度学习任务中，有时需要使用较小形状的 Tensor 与较大形状的 Tensor 执行计算，广播机制就是将较小形状的 Tensor 扩展到与较大形状的 Tensor 一样的形状，便于匹配计算，同时又没有对较小形状 Tensor 进行数据拷贝操作，从而提升算法实现的运算效率。 飞桨框架提供的一些 API 支持广播（broadcasting）机制，允许在一些运算时使用不同形状的 Tensor。 飞桨 Tensor 的广播机制主要遵循如下规则（参考 Numpy 广播机制）：

每个 Tensor 至少为一维 Tensor。

从最后一个维度向前开始比较两个 Tensor 的形状，需要满足如下条件才能进行广播：两个 Tensor 的维度大小相等；或者其中一个 Tensor 的维度等于 1；或者其中一个 Tensor 的维度不存在。

举例如下：

# 可以广播的例子 1
x = paddle.ones((2, 3, 4))
y = paddle.ones((2, 3, 4))
# 两个 Tensor 形状一致，可以广播
z = x + y
print(z.shape)
# [2, 3, 4]

# 可以广播的例子 2
x = paddle.ones((2, 3, 1, 5))
y = paddle.ones((3, 4, 1))
# 从最后一个维度向前依次比较：
# 第一次：y 的维度大小是 1
# 第二次：x 的维度大小是 1
# 第三次：x 和 y 的维度大小相等
# 第四次：y 的维度不存在
# 所以 x 和 y 是可以广播的
z = x + y
print(z.shape)
# [2, 3, 4, 5]

# 不可广播的例子
x = paddle.ones((2, 3, 4))
y = paddle.ones((2, 3, 6))
# 此时 x 和 y 是不可广播的，因为第一次比较：4 不等于 6
# z = x + y
# ValueError: (InvalidArgument) Broadcast dimension mismatch.

在了解两个 Tenso…
```

## 11. [test] AI框架与推理 — 参考段 `S07#3.3` + `S07#s142`

- 来源 S07；原文间隔 8；中文占比 A=0.334 B=0.401
- A 章节：Tensor 介绍 > 三、Tensor 的属性 > 3.3

```
初始化 Tensor 时可以通过 Tensor.place 来指定其分配的设备位置，可支持的设备位置有：CPU、GPU、固定内存、XPU（Baidu Kunlun）、NPU（Huawei）、MLU（寒武纪）、IPU（Graphcore）等。其中固定内存也称为不可分页内存或锁页内存，其与 GPU 之间具有更高的读写效率，并且支持异步传输，这对网络整体性能会有进一步提升，但其缺点是分配空间过多时可能会降低主机系统的性能，因为其减少了用于存储虚拟内存数据的可分页内存。

说明：

当未指定 place 时，Tensor 默认设备位置和安装的飞桨框架版本一致。如安装了 GPU 版本的飞桨，则设备位置默认为 GPU，即 Tensor 的place 默认为 paddle.CUDAPlace。

使用 paddle.device.set_device 可设置全局默认的设备位置。Tensor.place 的指定值优先级高于全局默认值。

以下示例分别创建了 CPU、GPU 和固定内存上的 Tensor，并通过 Tensor.place 查看 Tensor 所在的设备位置：

创建 CPU 上的 Tensor

cpu_Tensor = paddle.to_tensor(1, place=paddle.CPUPlace())
print(cpu_Tensor.place)

Place(cpu)

创建 GPU 上的 Tensor

gpu_Tensor = paddle.to_tensor(1, place=paddle.CUDAPlace(0))
print(gpu_Tensor.place) # 显示 Tensor 位于 GPU 设备的第 0 张显卡上

Place(gpu:0)

创建固定内存上的 Tensor

pin_memory_Tensor = paddle.to_tensor(1, place=paddle.CUDAPinnedPlace())
print(pin_memory_Tensor.place)

Place(gpu_pinned)
```
- B 章节：Tensor 介绍 > 六、Tensor 与 Numpy 数组相互转换

```
如果你已熟悉 Numpy，通过以下要点，可以方便地理解和迁移到 Tensor 的使用上：

Tensor 的很多基础操作 API 和 Numpy 在功能、用法上基本保持一致。如前文中介绍的指定数据、形状、区间创建 Tensor，Tensor 的形状、数据类型属性，Tensor 的各种操作，以及 Tensor 的广播，可以很方便地在 Numpy 中找到相似操作。

但是，Tensor 也有一些独有的属性和操作，而 Numpy 中没有对应概念或功能，这是为了更好地支持深度学习任务。如前文中介绍的通过图像、文本等原始数据手动或自动创建 Tensor 的功能，能够更便捷地处理数据，Tensor 的设备位置属性，可以很方便地将 Tensor 迁移到 GPU 或各种 AI 加速硬件上，Tensor 的 stop_gradient 属性，也是 Tensor 独有的，以便更好地支持深度学习任务。

如果已有 Numpy 数组，可使用 paddle.to_tensor 创建任意维度的 Tensor，创建的 Tensor 与原 Numpy 数组具有相同的形状与数据类型。

tensor_temp = paddle.to_tensor(np.array([1.0, 2.0]))
print(tensor_temp)

Tensor(shape=[2], dtype=float64, place=Place(gpu:0), stop_gradient=True,
[1., 2.])

注意：

基于 Numpy 数组创建 Tensor 时，飞桨是通过拷贝方式创建，与原始数据不共享内存。

相对应地，飞桨也支持将 Tensor 转换为 Numpy 数组，可通过 Tensor.numpy 方法实现。

tensor_to_convert = paddle.to_tensor([1.,2.])
tensor_to_convert.numpy()

array([1., 2.], dtype=float32)
```

## 12. [test] AI框架与推理 — 参考段 `S06#s115` + `S06#3.4.1`

- 来源 S06；原文间隔 6；中文占比 A=0.247 B=0.502
- A 章节：10分钟快速上手飞桨 > 三、实践：手写数字识别任务

```
『手写数字识别』是深度学习里的 Hello World 任务，用于对 0 ~ 9 的十类数字进行分类，即输入手写数字的图片，可识别出这个图片中的数字。

本任务用到的数据集为 MNIST 手写数字数据集，用于训练和测试模型。该数据集包含 60000 张训练图片、 10000 张测试图片、以及对应的分类标签文件，每张图片上是一个 0 ~ 9 的手写数字，分辨率为 28 * 28。部分图像和对应的分类标签如下图所示。

图 1：MNIST 数据集样例

开始之前，需要使用下面的命令安装 Python 的 matplotlib 库和 numpy 库，matplotlib 库用于可视化图片，numpy 库用于处理数据。

[3]:

# 使用 pip 工具安装 matplotlib 和 numpy
! python3 -m pip install matplotlib numpy -i https://mirror.baidu.com/pypi/simple

下面是手写数字识别任务的完整代码，如果想直接运行代码，可以拷贝下面的完整代码到一个Python文件中运行。

[5]:

import paddle
import numpy as np
from paddle.vision.transforms import Normalize

transform = Normalize(mean=[127.5], std=[127.5], data_format="CHW")
# 下载数据集并初始化 DataSet
train_dataset = paddle.vision.datasets.MNIST(mode="train", transform=transform)
test_dataset = paddle.vision.datasets.MNIST(mode="test", transform=transform)

# 模型组网并初始化网络
lenet = paddle.vision.models.LeNet(num_classes=10)
model = paddle…
```
- B 章节：10分钟快速上手飞桨 > 三、实践：手写数字识别任务 > 3.4 > 3.4.1

```
模型训练完成后，通常需要将训练好的模型参数和优化器等信息，持久化保存到参数文件中，便于后续执行推理验证。

在飞桨中可通过调用 paddle.Model.save 保存模型。代码示例如下，其中 output 为模型保存的文件夹名称，minst 为保存的模型文件名称。

[5]:

# 保存模型，文件夹会自动创建
model.save("./output/mnist")

以上代码执行后会在output目录下保存两个文件，mnist.pdopt为优化器的参数，mnist.pdparams为模型的参数。

output
├── mnist.pdopt # 优化器的参数
└── mnist.pdparams # 模型的参数
```

## 13. [test] AI框架与推理 — 参考段 `S06#3.3.1` + `S06#3.4.2`

- 来源 S06；原文间隔 3；中文占比 A=0.249 B=0.271
- A 章节：10分钟快速上手飞桨 > 三、实践：手写数字识别任务 > 3.3 > 3.3.1

```
模型训练需完成如下步骤：

使用paddle.Model封装模型。 将网络结构组合成可快速使用 飞桨高层 API 进行训练、评估、推理的实例，方便后续操作。

使用paddle.Model.prepare完成训练的配置准备工作。 包括损失函数、优化器和评价指标等。飞桨在 paddle.optimizer 下提供了优化器算法相关 API，在 paddle.nn Loss层 提供了损失函数相关 API，在 paddle.metric 下提供了评价指标相关 API。

使用paddle.Model.fit配置循环参数并启动训练。 配置参数包括指定训练的数据源 train_dataset、训练的批大小 batch_size、训练轮数 epochs 等，执行后将自动完成模型的训练循环。

因为是分类任务，这里损失函数使用常见的 CrossEntropyLoss （交叉熵损失函数），优化器使用 Adam，评价指标使用 Accuracy 来计算模型在训练集上的精度。

[5]:

# 封装模型，便于进行后续的训练、评估和推理
model = paddle.Model(lenet)

# 模型训练的配置准备，准备损失函数，优化器和评价指标
model.prepare(
paddle.optimizer.Adam(parameters=model.parameters()),
paddle.nn.CrossEntropyLoss(),
paddle.metric.Accuracy(),
)

# 开始训练
model.fit(train_dataset, epochs=5, batch_size=64, verbose=1)

The loss value printed in the log is the current step, and the metric is the average value of previous steps.
Epoch 1/5
step 938/938 [==============================] - loss: 0.0011 - …
```
- B 章节：10分钟快速上手飞桨 > 三、实践：手写数字识别任务 > 3.4 > 3.4.2

```
执行模型推理时，可调用 paddle.Model.load 加载模型，然后即可通过 paddle.Model.predict_batch 执行推理操作。

如下示例中，针对前面创建的 model 网络加载保存的参数文件 output/mnist，并选择测试集中的一张图片 test_dataset[0] 作为输入，执行推理并打印结果，可以看到推理的结果与可视化图片一致。

[10]:

# 加载模型
model.load("output/mnist")

# 从测试集中取出一张图片
img, label = test_dataset[0]
# 将图片shape从1*28*28变为1*1*28*28，增加一个batch维度，以匹配模型输入格式要求
img_batch = np.expand_dims(img.astype("float32"), axis=0)

# 执行推理并打印结果，此处predict_batch返回的是一个list，取出其中数据获得预测结果
out = model.predict_batch(img_batch)[0]
pred_label = out.argmax()
print("true label: {}, pred label: {}".format(label[0], pred_label))
# 可视化图片
from matplotlib import pyplot as plt

plt.imshow(img[0])

true label: 7, pred label: 7

[10]:

<matplotlib.image.AxesImage at 0x7f853e6f0e50>

更多参考： * 模型保存与加载 * 模型训练、评估与推理
```

## 14. [dev] 测试自动化 — 参考段 `S14#s244` + `S14#s263~2`

- 来源 S14；原文间隔 23；中文占比 A=0.334 B=0.33
- A 章节：Pytest夹具：显式、模块化、可扩展¶ > 自动使用设备(您不必请求设备)¶

```
有时，您可能希望有一个(甚至几个)您知道所有测试都将依赖的设备。“自动”灯具是一种自动进行所有测试的便捷方式 请求 他们。这可以去掉很多多余的东西 请求 ，甚至可以提供更高级的夹具用法(下面有更多信息)。

我们可以通过传入使设备成为自动设备。 autouse=True 给灯具的装饰师。下面是一个如何使用它们的简单示例：

# contents of test_append.py
import pytest

@pytest.fixture
def first_entry():
return "a"

@pytest.fixture
def order(first_entry):
return []

@pytest.fixture(autouse=True)
def append_first(order, first_entry):
return order.append(first_entry)

def test_string_only(order, first_entry):
assert order == [first_entry]

def test_string_and_int(order, first_entry):
order.append(2)
assert order == [first_entry, 2]

在这个例子中， append_first 灯具是一种自动使用的灯具。因为它是自动发生的，所以这两个测试都会受到它的影响，即使这两个测试都不是 已请求 它。这并不意味着他们 不能 是 已请求 不过，只是它不是 必要的 .
```
- B 章节：Pytest夹具：显式、模块化、可扩展¶ > 运行多个 assert 安全的报表¶

```
def test_profile_link(self, landing_page, user):
profile_href = urljoin(base_url, f"/profile?id={user.profile_id}")
assert landing_page.profile_link.get_attribute("href") == profile_href

请注意，这些方法仅引用 self 在签名中作为一种形式。没有任何状态绑定到实际测试类，因为它可能在 unittest.TestCase 框架。一切都由最火爆的夹具系统管理。

每个方法只需请求它实际需要的装置，而不用担心顺序。这是因为 act Fitture是一个自动选择的装置，它确保所有其他装置在它之前执行。不需要进行更多的状态更改，因此测试可以随意进行任意数量的不更改状态的查询，而不会冒着触碰其他测试的风险。

这个 login Fixture也是在类中定义的，因为不是模块中的每个其他测试都期望成功登录，并且 act 对于另一个测试类，可能需要稍微不同地处理。例如，如果我们想要编写另一个关于提交错误凭据的测试场景，我们可以通过向测试文件添加类似以下内容来处理：

class TestLandingPageBadCredentials:
@pytest.fixture(scope="class")
def faux_user(self, user):
_user = deepcopy(user)
_user.password = "badpass"
return _user

def test_raises_bad_credentials_exception(self, login_page, faux_user):
with pytest.raises(BadCredentialsException):
login_page.login(faux_user)
```

## 15. [dev] 测试自动化 — 参考段 `S14#s243` + `S14#s262~2`

- 来源 S14；原文间隔 22；中文占比 A=0.318 B=0.258
- A 章节：Pytest夹具：显式、模块化、可扩展¶ > “请求”装置¶ > 固定装置可以是 已请求 每个测试不止一次(缓存返回值)¶

```
固定装置也可以 已请求 在同一测试期间多次执行，pytest不会为该测试再次执行它们。这意味着我们可以 请求 依赖于它们的多个装置中的装置(甚至在测试本身中也是如此)，而这些装置没有多次执行。

# contents of test_append.py
import pytest

# Arrange
@pytest.fixture
def first_entry():
return "a"

# Arrange
@pytest.fixture
def order():
return []

# Act
@pytest.fixture
def append_first(order, first_entry):
return order.append(first_entry)

def test_string_only(append_first, order, first_entry):
# Assert
assert order == [first_entry]

如果A 已请求 每执行一次装置，就会执行一次 已请求 在测试期间，此测试将失败，因为 append_first 和 test_string_only 会看到 order 作为空列表(即 [] )，但由于 order 在第一次调用它之后被缓存(连同执行它可能具有的任何副作用)，测试和 append_first 都引用了相同的对象，测试看到了效果 append_first 带在那个物体上。
```
- B 章节：Pytest夹具：显式、模块化、可扩展¶ > 卫浴装置实例化顺序¶ > 自动设备首先在其作用域内执行¶

```
Even though nothing in TestClassWithC1Request is requesting c1, it still
is executed for the tests inside it anyway:

但是，仅仅因为一个自动设备请求了非自动设备，这并不意味着非自动设备成为它可以应用到的所有上下文中的自动设备。它只能有效地成为实际自动使用装置(请求非自动装置的装置)可以应用的上下文的自动使用装置。

例如，查看此测试文件：

import pytest

@pytest.fixture
def order():
return []

@pytest.fixture
def c1(order):
order.append("c1")

@pytest.fixture
def c2(order):
order.append("c2")

class TestClassWithAutouse:
@pytest.fixture(autouse=True)
def c3(self, order, c2):
order.append("c3")

def test_req(self, order, c1):
assert order == ["c2", "c3", "c1"]

def test_no_req(self, order):
assert order == ["c2", "c3"]

class TestClassWithoutAutouse:
def test_req(self, order, c1):
assert order == ["c1"]

def test_no_req(self, order):
assert order == []

它会分解成这样的东西：

为了 test_req 和 test_no_req 里面 TestClassWithAutouse ， c3 有效地使 c2 一个自动开关装置，这就是为什么 c2 和 c3 在未被请求的情况下为这两个测试执行，以及为什么 c2 和 c3 都是在执行之前执行的 c1 …
```

## 16. [dev] 测试自动化 — 参考段 `S14#s241` + `S14#s261`

- 来源 S14；原文间隔 22；中文占比 A=0.243 B=0.295
- A 章节：Pytest夹具：显式、模块化、可扩展¶ > “请求”装置¶ > 固定装置是可重复使用的¶

```
使pytest的装置系统如此强大的原因之一是，它使我们能够定义可以重复使用的通用设置步骤，就像使用普通函数一样。两个不同的测试可以请求相同的装置，并让pytest从该装置给出各自的测试结果。

这对于确保测试不受彼此影响非常有用。我们可以使用这个系统来确保每个测试都获得自己的新批次数据，并且是从干净的状态开始的，这样它就可以提供一致的、可重复的结果。

下面是一个如何派上用场的例子：

# contents of test_append.py
import pytest

# Arrange
@pytest.fixture
def first_entry():
return "a"

# Arrange
@pytest.fixture
def order(first_entry):
return [first_entry]

def test_string(order):
# Act
order.append("b")

# Assert
assert order == ["a", "b"]

def test_int(order):
# Act
order.append(2)

# Assert
assert order == ["a", 2]

这里的每个测试都有自己的副本 list 对象，这意味着 order Fixture正在执行两次(同样的情况也适用于 first_entry 固定装置)。如果我们也要手工完成这项工作，它将如下所示：

def first_entry():
return "a"

def order(first_entry):
return [first_entry]

def test_string(order):
# Act
order.append("b")

# Assert
assert order == ["a", "b"]

def test_int(order):
# Act
order.append(2)

# Assert
assert order == ["a", 2]

entry = first_entry()
the…
```
- B 章节：Pytest夹具：显式、模块化、可扩展¶ > 卫浴装置实例化顺序¶ > 相同顺序的装置基于依赖关系执行¶

```
When a fixture requests another fixture, the other fixture is executed first.
So if fixture a requests fixture b, fixture b will execute first,
because a depends on b and can't operate without it. Even if a
doesn't need the result of b, it can still request b if it needs to make
sure it is executed after b.

例如：

import pytest

@pytest.fixture
def order():
return []

@pytest.fixture
def a(order):
order.append("a")

@pytest.fixture
def b(a, order):
order.append("b")

@pytest.fixture
def c(a, b, order):
order.append("c")

@pytest.fixture
def d(c, b, order):
order.append("d")

@pytest.fixture
def e(d, b, order):
order.append("e")

@pytest.fixture
def f(e, order):
order.append("f")

@pytest.fixture
def g(f, c, order):
order.append("g")

def test_order(g, order):
assert order == ["a", "b", "c", "d", "e", "f", "g"]

如果我们画出什么取决于什么，我们就会得到类似这样的东西：

每个灯具提供的规则(关于每个灯具必须遵循哪些灯具)足够全面，可以将其展平为：

必须通过这些请求…
```

## 17. [dev] 测试自动化 — 参考段 `S14#s238` + `S14#s257`

- 来源 S14；原文间隔 21；中文占比 A=0.61 B=0.284
- A 章节：Pytest夹具：显式、模块化、可扩展¶ > “请求”装置¶

```
所以固定装置就是我们 准备 对于一个测试，但是我们如何告诉pytest哪些测试和装置需要哪些装置呢？

在基本级别上，测试函数通过将fixture声明为参数来请求fixture，如 test_my_fruit_in_basket(my_fruit, fruit_basket): 在前面的示例中。

在基本级别上，pytest依赖于一个测试来告诉它它需要什么装置，所以我们必须将该信息构建到测试本身中。我们必须进行测试。“ 请求 它所依赖的装置，要做到这一点，我们必须将这些装置作为参数列在测试函数的“签名”中(这是 def test_something(blah, stuff, more): 线）。

当pytest运行测试时，它会查看该测试函数签名中的参数，然后搜索与这些参数同名的fixture。一旦pytest找到它们，它就会运行这些装置，捕获它们返回的内容(如果有的话)，并将这些对象作为参数传递给测试函数。
```
- B 章节：Pytest夹具：显式、模块化、可扩展¶ > 夹具可用性¶ > 来自第三方插件的装置¶

```
不过，不必在此结构中定义装置即可用于测试。它们也可以由安装的第三方插件提供，这就是许多最火爆的插件的运行方式。只要安装了这些插件，就可以从测试套件中的任何位置请求它们提供的夹具。

因为它们是从测试套件的结构之外提供的，所以第三方插件并不能真正提供这样的范围 conftest.py 测试套件中的文件和目录可以做到这一点。因此，pytest将如前所述通过作用域搜索跳出的装置，仅到达插件中定义的装置 last .

例如，给定以下文件结构：

tests/
__init__.py

conftest.py
# content of tests/conftest.py
import pytest

@pytest.fixture
def order():
return []

subpackage/
__init__.py

conftest.py
# content of tests/subpackage/conftest.py
import pytest

@pytest.fixture(autouse=True)
def mid(order, b_fix):
order.append("mid subpackage")

test_subpackage.py
# content of tests/subpackage/test_subpackage.py
import pytest

@pytest.fixture
def inner(order, mid, a_fix):
order.append("inner subpackage")

def test_order(order, inner):
assert order == ["b_fix", "mid subpackage", "a_fix", "inner subpackage"]

如果 plugin_a 已安装，并提供夹具 a_fix 和 plugin_b 已安装，并提供夹具 b_fix ，则测试对灯具的搜索将如下所示：

pytest将仅搜索 a_fix 和 b_fix 在插件中，先在里面的作用域中搜索…
```

## 18. [test] 测试自动化 — 参考段 `S14#s240` + `S14#s259`

- 来源 S14；原文间隔 21；中文占比 A=0.284 B=0.439
- A 章节：Pytest夹具：显式、模块化、可扩展¶ > “请求”装置¶ > 装置可以 请求 其他固定装置¶

```
pytest最大的优势之一是其极其灵活的夹具系统。它允许我们将复杂的测试需求归结为更简单、更有组织的功能，在这些功能中，我们只需要让每个功能描述它们所依赖的东西。我们将更深入地介绍这一点，但现在，这里有一个快速示例来演示装置如何使用其他装置：

# contents of test_append.py
import pytest

# Arrange
@pytest.fixture
def first_entry():
return "a"

# Arrange
@pytest.fixture
def order(first_entry):
return [first_entry]

def test_string(order):
# Act
order.append("b")

# Assert
assert order == ["a", "b"]

请注意，这与上面的示例相同，但几乎没有更改。火柴里的固定装置 请求 固定装置就像测试一样。尽管如此， 请求 规则适用于进行测试的夹具。下面是如果我们手动完成此示例的工作方式：

def first_entry():
return "a"

def order(first_entry):
return [first_entry]

def test_string(order):
# Act
order.append("b")

# Assert
assert order == ["a", "b"]

entry = first_entry()
the_list = order(first_entry=entry)
test_string(order=the_list)
```
- B 章节：Pytest夹具：显式、模块化、可扩展¶ > 卫浴装置实例化顺序¶

```
When pytest wants to execute a test, once it knows what fixtures will be
executed, it has to figure out the order they'll be executed in. To do this, it
considers 3 factors:

范围

依赖项

汽车旅馆

除了重合之外，装置或测试的名称、它们的定义位置、定义顺序以及请求装置的顺序都不会影响执行顺序。虽然pytest会努力确保这样的巧合在一次又一次的运行中保持一致，但这不是应该依赖的东西。如果您想要控制顺序，最安全的方法是依赖这3件事，并确保清楚地建立了依赖关系。
```

## 19. [test] 测试自动化 — 参考段 `S14#s237` + `S14#s255`

- 来源 S14；原文间隔 20；中文占比 A=0.372 B=0.447
- A 章节：Pytest夹具：显式、模块化、可扩展¶ > 固定装置是什么？¶ > 返回到固定装置¶

```
从字面意义上讲，“装置”是每个 安排 步骤和数据。它们是测试完成任务所需的一切。

在基本级别上，测试函数通过将fixture声明为参数来请求fixture，如 test_ehlo(smtp_connection): 在前面的示例中。

在pytest中，“fixture”是您定义的用于此目的的函数。但它们不一定要局限于 安排 台阶。他们可以提供 act 步骤，对于设计更复杂的测试来说，这可能是一项强大的技术，特别是考虑到pytest的装置系统是如何工作的。但我们会更深入地探讨这一点。

我们可以告诉pytest一个特定的函数是一个fixture，方法是用 @pytest.fixture 。下面是一个简单的示例，说明pytest中的fixture可能是什么样子：

import pytest

class Fruit:
def __init__(self, name):
self.name = name

def __eq__(self, other):
return self.name == other.name

@pytest.fixture
def my_fruit():
return Fruit("apple")

@pytest.fixture
def fruit_basket(my_fruit):
return [Fruit("banana"), my_fruit]

def test_my_fruit_in_basket(my_fruit, fruit_basket):
assert my_fruit in fruit_basket

测试也不必局限于单个装置。它们可以依赖于您想要的任意多个装置，装置也可以使用其他装置。这才是pytest的夹具系统真正闪耀的地方。

如果能让事情变得更干净，不要害怕拆散。
```
- B 章节：Pytest夹具：显式、模块化、可扩展¶ > 夹具可用性¶

```
夹具可用性是从测试的角度确定的。装置只有在定义装置的范围内时才可供测试请求。如果在类内定义了装置，则只能由该类内的测试请求它。但是，如果在模块的全局范围内定义了一个装置，那么该模块中的每个测试，即使它是在一个类中定义的，也可以请求它。

类似地，如果测试与定义自动使用夹具的范围相同，则该测试也只能受到自动使用夹具的影响(请参见 自动设备首先在其作用域内执行 ）

一个装置也可以请求任何其他装置，不管它是在哪里定义的，只要请求它们的测试可以看到所有涉及的装置。

例如，下面是一个测试文件，其中包含一个装置 (outer )需要固定装置 (inner )来自未在中定义的作用域：

import pytest

@pytest.fixture
def order():
return []

@pytest.fixture
def outer(order, inner):
order.append("outer")

class TestOne:
@pytest.fixture
def inner(self, order):
order.append("one")

def test_order(self, order, outer):
assert order == ["one", "outer"]

class TestTwo:
@pytest.fixture
def inner(self, order):
order.append("two")

def test_order(self, order, outer):
assert order == ["two", "outer"]

从测试的角度来看，他们可以毫不费力地看到他们所依赖的每一个灯具：

所以当他们跑的时候， outer 会毫不费力地找到 inner ，因为pytest从测试的角度进行搜索。

注解

定义装置的作用域与实例化该装置的顺序无关：该顺序由所描述的逻辑强制执行 here .
```

## 20. [test] 测试自动化 — 参考段 `S14#s236` + `S14#s254`

- 来源 S14；原文间隔 19；中文占比 A=0.8 B=0.301
- A 章节：Pytest夹具：显式、模块化、可扩展¶ > 固定装置是什么？¶

```
在我们深入了解固定器是什么之前，让我们先来看看什么是测试。

最简单地说，测试的目的是查看特定行为的结果，并确保结果与您的预期一致。行为不是可以通过经验来衡量的，这就是为什么编写测试会很有挑战性的原因。

“行为”是指某些系统 作为回应 特定的情况和/或刺激。但确切地说 how 或 why 做了一些事情并不像做了什么那么重要 what 已经完成了。

您可以认为测试分为四个步骤：

Arrange

Act

Assert

Cleanup

安排 是我们为考试做准备的地方。这意味着几乎所有的东西，除了“ act “它把多米诺骨牌排成一排，这样 act 可以在一个改变状态的步骤中完成它的事情。这可能意味着准备对象、启动/终止服务、向数据库中输入记录，甚至是定义要查询的URL、为尚不存在的用户生成一些凭据，或者只是等待某个过程完成。

Act 是启动 行为 我们想测试一下。这一行为实现了被测系统(SUT)状态的改变，也是我们可以查看的改变后的状态，以便我们对行为做出判断。这通常采用函数/方法调用的形式。

断言 是我们观察结果状态的地方，检查尘埃落定后它看起来是否像我们预期的那样。这是我们收集证据来证明行为是否符合我们预期的地方。这个 assert 在我们的测试中，我们在哪里进行测量/观察，并对其应用我们的判断。如果什么东西应该是绿色的，我们会说 assert thing == "green" .

清理 是测试在其自身之后重新开始的位置，因此其他测试不会意外地受到它的影响。

在它的核心，测试最终是 act 和 断言 步骤，使用 安排 仅提供上下文的步骤。 行为 存在于 act 和 断言 .
```
- B 章节：Pytest夹具：显式、模块化、可扩展¶ > 安全拆卸¶ > 安全夹具结构¶

```
最安全和最简单的装置结构要求将装置限制为每个装置只能执行一个状态更改操作，然后将它们与它们的拆卸代码捆绑在一起，如下所示 the email examples above 显示出来了。

状态更改操作可能失败但仍修改状态的可能性是不可能的，因为这些操作中的大多数往往都是这样 transaction -基于(至少在状态可能被抛在后面的测试级别)。因此，如果我们将任何成功的状态更改操作移动到单独的装置函数，并将其与其他可能失败的状态更改操作分开，以确保任何成功的状态更改操作被拆除，那么我们的测试将最有可能离开它们发现的测试环境。

例如，假设我们有一个网站，其中有一个登录页面，并且我们可以访问管理API，在那里我们可以生成用户。对于我们的测试，我们想要：

通过该管理API创建用户

使用Selenium启动浏览器

进入我们网站的登录页

以我们创建的用户身份登录

声明他们的名字在登录页的页眉中

我们不想让该用户留在系统中，也不想让浏览器会话保持运行，所以我们希望确保创建这些内容的fixture在它们自己之后被清除。

这看起来可能是这样的：

注解

在本例中，某些固定装置(即 base_url 和 admin_credentials )隐含着存在于其他地方。所以现在，让我们假设它们存在，我们只是不去看它们。

from uuid import uuid4
from urllib.parse import urljoin

from selenium.webdriver import Chrome
import pytest

from src.utils.pages import LoginPage, LandingPage
from src.utils import AdminApiClient
from src.utils.data_types import User

@pytest.fixture
def admin_client(base_url, admin_credentials):
return AdminApiClient(base_url, *…
```

## 21. [test] 测试自动化 — 参考段 `S14#s235` + `S14#s253`

- 来源 S14；原文间隔 19；中文占比 A=0.508 B=0.294
- A 章节：Pytest夹具：显式、模块化、可扩展¶

```
Software test fixtures 初始化测试功能。它们提供了一个固定的基线，以便测试可靠地执行并产生一致的、可重复的结果。初始化可以设置服务、状态或其他操作环境。在fixture函数中，每个函数的参数通常在test之后被命名为fixture。

pytest fixtures相对于传统的xUnit风格的setup/teardown函数提供了显著的改进：

装置有明确的名称，通过声明它们在测试函数、模块、类或整个项目中的使用来激活。

夹具以模块化的方式实现，因为每个夹具名称触发 夹具功能 可以使用其他固定装置。

夹具管理从简单的单元扩展到复杂的功能测试，允许根据配置和组件选项参数化夹具和测试，或者跨功能、类、模块或整个测试会话范围重复使用夹具。

无论使用多少夹具，拆卸逻辑都可以轻松、安全地进行管理，无需手动仔细处理错误或微观管理添加清理步骤的顺序。

此外，pytest继续支持 经典的Xunit风格设置 . 您可以混合这两种样式，根据您的喜好，逐步从经典样式移动到新样式。你也可以从现有的 unittest.TestCase style 或 nose based 项目。

Fixtures 使用 @pytest.fixture 装饰者， described below . Pytest有有用的内置设备，下面列出以供参考：

capfd

以文本形式捕获输出到文件描述符 1 和 2 .

capfdbinary

以字节形式捕获输出到文件描述符 1 和 2 .

caplog

控制日志记录和访问日志条目。

capsys

捕获，作为文本，输出到 sys.stdout 和 sys.stderr .

capsysbinary

以字节形式捕获输出到 sys.stdout 和 sys.stderr .

cache

跨pytest运行存储和检索值。

doctest_namespace

提供注入到docstests命名空间中的dict。

monkeypatch

临时修改类、函数、字典， os.environ ，以及其他对象。

pytestconfi…
```
- B 章节：Pytest夹具：显式、模块化、可扩展¶ > 安全拆卸¶

```
PYTEST的夹具系统是 very 功能强大，但它仍然由计算机运行，所以它无法弄清楚如何安全地拆卸我们扔向它的所有东西。如果我们不小心，错误位置的错误可能会留下我们测试中的东西，这可能很快就会导致进一步的问题。

例如，考虑以下测试(基于上面的邮件示例)：

import pytest

from emaillib import Email, MailAdminClient

@pytest.fixture
def setup():
mail_admin = MailAdminClient()
sending_user = mail_admin.create_user()
receiving_user = mail_admin.create_user()
email = Email(subject="Hey!", body="How's it going?")
sending_user.send_emai(email, receiving_user)
yield receiving_user, email
receiving_user.delete_email(email)
admin_client.delete_user(sending_user)
admin_client.delete_user(receiving_user)

def test_email_received(setup):
receiving_user, email = setup
assert email in receiving_user.inbox

这个版本要紧凑得多，但也更难阅读，没有非常具描述性的装置名称，而且所有装置都不能很容易地重用。

还有一个更严重的问题，那就是如果设置中的任何一个步骤引发异常，则所有tearDown代码都不会运行。

一种选择可能是使用 addfinalizer 方法，而不是收益固定器，但这可能会变得相当复杂和难以维护(而且它将不再紧凑)。
```

## 22. [dev] 嵌入式开发 — 参考段 `S02#s8` + `S02#s25`

- 来源 S02；原文间隔 17；中文占比 A=0.561 B=0.598
- A 章节：SPI 主机驱动程序 > 术语

```
下表为 SPI 主机驱动的相关术语。

术语

定义

主机 (Host)

ESP32 内置的 SPI 控制器外设。用作 SPI 主机，在总线上发起 SPI 传输。

设备 (Device)

SPI 从机设备。一条 SPI 总线与一或多个设备连接。每个设备共享 MOSI、MISO 和 SCLK 信号，但只有当主机向设备的专属 CS 线发出信号时，设备才会在总线上处于激活状态。

总线 (Bus)

信号总线，由连接到同一主机的所有设备共用。一般来说，每条总线包括以下线：MISO、MOSI、SCLK、一条或多条 CS 线，以及可选的 QUADWP 和 QUADHD。因此，除每个设备都有单独的 CS 线外，所有设备都连接在相同的线下。多个设备也可以菊花链的方式共享一条 CS 线。

MOSI

主机输出，从机输入，也写作 D。数据从主机发送至设备。在 Octal/OPI 模式下也表示为 data0 信号。

MISO

主机输入，从机输出，也写作 Q。数据从设备发送至主机。在 Octal/OPI 模式下也表示为 data1 信号。

SCLK

串行时钟。由主机产生的振荡信号，使数据位的传输保持同步。

CS

片选。允许主机选择连接到总线上的单个设备，以便发送或接收数据。

QUADWP

写保护信号。只用于 4 位 (qio/qout) 传输。在 Octal/OPI 模式下也表示为 data2 信号。

QUADHD

保持信号。只用于 4 位 (qio/qout) 传输。在 Octal/OPI 模式下也表示为 data3 信号。

DATA4

在 Octal/OPI 模式下表示为 data4 信号。

DATA5

在 Octal/OPI 模式下表示为 data5 信号。

DATA6

在 Octal/OPI 模式下表示为 data6 信号。

DATA7

在 Octal/OPI 模式下表示为 data7 信号。

断言 (Assertion)

指激活一条线路的操作。

去断言 (De-assertion)

指将线路恢复到非活动状态（回到空闲状态）的操作…
```
- B 章节：SPI 主机驱动程序 > 传输速度的影响因素 > SPI 时钟频率

```
GPSPI 外设的时钟源可以通过设置 spi_device_interface_config_t::clock_source 选择，可用的时钟源请参阅 spi_clock_source_t。

默认情况下，驱动程序将把时钟源设置为 SPI_CLK_SRC_DEFAULT。这往往代表 GPSPI 可选时钟源中的最高频率，在不同的芯片上这一数值会有所不同。

设备的实际时钟频率可能不完全等于所设置的数字，驱动会将其重新计算为与硬件兼容的最接近的数字，并且不超过时钟源的时钟频率。调用函数 spi_device_get_actual_freq() 以了解驱动计算的实际频率。

设备的时钟频率可在传输过程中实时更改，可以通过设置 spi_transaction_t::override_freq_hz 实现，此操作将为该设备的该次及以后的传输使用新的时钟频率。若某次期望设置的时钟频率无法实现，驱动将打印警告并继续使用之前的时钟频率进行传输。

写入或读取阶段的理论最大传输速度可根据下表计算：

写入/读取阶段的线宽

速度 (Bps)

1-Line

SPI 频率 / 8

2-Line

SPI 频率 / 4

4-Line

SPI 频率 / 2

其他阶段（命令阶段、地址阶段、Dummy 阶段）的传输速度计算与此类似。

时钟频率过高可能会限制部分功能的使用，请参阅 时序影响因素。
```

## 23. [dev] 嵌入式开发 — 参考段 `S02#s9` + `S02#s26`

- 来源 S02；原文间隔 17；中文占比 A=0.676 B=0.444
- A 章节：SPI 主机驱动程序 > 主机驱动特性

```
SPI 主机驱动程序负责管理主机与设备间的通信，具有以下特性：

支持多线程环境使用

读写数据过程中 DMA 透明传输

同一信号总线上不同设备的数据可自动时分复用，请参阅 SPI 总线锁。

警告

SPI 主机驱动允许总线上连接多个设备（共享单个 ESP32 SPI 外设）。每个设备仅由一个任务访问时，驱动程序线程安全。反之，若多个任务尝试访问同一 SPI 设备，则驱动程序 非线程安全。此时，建议执行以下任一操作：

重构应用程序，确保每个 SPI 外设在同一时间仅由一个任务访问。使用 spi_bus_config_t::isr_cpu_id 将 SPI ISR 注册到与 SPI 外设相关任务相同的内核，以确保线程安全。

使用 xSemaphoreCreateMutex 为共享设备添加互斥锁。
```
- B 章节：SPI 主机驱动程序 > 传输速度的影响因素 > 缓存缺失

```
默认配置只将 ISR 置于 IRAM 中。其他 SPI 相关功能，包括驱动本身和回调都可能发生缓存缺失，需等待代码从 flash 中读取。为避免缓存缺失，可参考 CONFIG_SPI_MASTER_IN_IRAM，将整个 SPI 驱动置入 IRAM，并将整个回调及其 callee 函数一起置入 IRAM。

备注

SPI 驱动是基于 FreeRTOS 的 API 实现的，在使用 CONFIG_SPI_MASTER_IN_IRAM 时，应启用 CONFIG_FREERTOS_IN_IRAM。

单个中断传输事务传输 n 字节的总成本为 20+8n/Fspi[MHz] [µs]，故传输速度为 n/(20+8n/Fspi)。8 MHz 时钟速度的传输速度见下表。

频率 (MHz)

传输事务间隔 (µs)

传输事务长度 (bytes)

传输时长 (µs)

传输速度 (KBps)

25

26

38.5

25

33

242.4

25

16

41

490.2

25

64

89

719.1

25

128

153

836.6

传输事务长度较短时将提高传输事务间隔成本，因此应尽可能将几个短传输事务压缩成一个传输事务，以提升传输速度。

注意，ISR 在 flash 操作期间默认处于禁用状态。要在 flash 操作期间继续发送传输事务，请启用 CONFIG_SPI_MASTER_ISR_IN_IRAM，并在 spi_bus_config_t::intr_flags 中设置 ESP_INTR_FLAG_IRAM。此时，flash 操作前列队的传输事务将由 ISR 并行处理。此外，每个设备的回调和它们的 callee 函数都应该在 IRAM 中，避免回调因缓存丢失而崩溃。详情请参阅 IRAM 安全中断处理程序。
```

## 24. [test] 嵌入式开发 — 参考段 `S02#s18` + `S02#s27`

- 来源 S02；原文间隔 9；中文占比 A=0.368 B=0.572
- A 章节：SPI 主机驱动程序 > 使用驱动程序 > 传输数据小于 32 位的传输事务

```
当传输事务数据等于或小于 32 位时，为数据分配一个缓冲区将是次优的选择。实际上，数据可以直接存储于传输事务结构体中。对已传输的数据，可通过调用函数 spi_transaction_t::tx_data 并在传输时设置 SPI_TRANS_USE_TXDATA 标志信号来实现。对已接收的数据，可通过调用函数 spi_transaction_t::rx_data 并设置 SPI_TRANS_USE_RXDATA 来实现。在这两种情况下，请勿修改 spi_transaction_t::tx_buffer 或 spi_transaction_t::rx_buffer，因为它们与 spi_transaction_t::tx_data 和 spi_transaction_t::rx_data 的内存位置相同。
```
- B 章节：SPI 主机驱动程序 > 时序影响因素

```
如图所示， SCLK 发射沿之后、信号被内部寄存器锁存之前， MISO 线存在延迟。因此， MISO 管脚的设置时间是 SPI 时钟速度的限制因素。当延迟过长时，设置松弛度 < 0 ，违反了设置时序要求，读取可能有误。

最大有效频率取决于：

spi_device_interface_config_t::input_delay_ns - SCLK 上的一个时钟周期开始后 MISO 总线上的最大数据有效时间

是否使用了 IO_MUX 管脚或 GPIO 矩阵

使用 GPIO 矩阵时，最大有效频率降至现有 输入延迟 的 33 ~ 77%。使用 IO_MUX 管脚或 dummy 位 可以保留较高的频率，调用函数 spi_get_freq_limit() 能够获取主机的最大读取频率。

Dummy 位：在读取阶段开始之前，可以插入 Dummy 时钟，在此期间，主机不读取数据。设备能看到 Dummy 时钟并发出数据，但主机在读取阶段之前不会读取。这就弥补了主机 MISO 设置时间不足的问题，并使主机达到更高的读取频率。

理想状态下，如果设备的速度足以使输入延迟控制在一个 APB 时钟周期 (12.5 ns) 以内，则在不同的条件下，主机读取（或读取和写入）的最大频率如下表所示：

频率限制 (MHz)

频率限制 (MHz)

驱动是否使用 Dummy 位

备注

GPIO 矩阵

IO_MUX 管脚

26.6

80

40

--

半双工，禁用 DMA

如果主机仅写入数据，可以通过设置 spi_device_interface_config_t::flags 中的 SPI_DEVICE_NO_DUMMY 位来禁用 Dummy 位 和频率检查机制。禁用 Dummy 位 和频率检查机制后，即便在使用 GPIO 矩阵的情况下，输出频率也可达 80 MHz。

spi_device_interface_config_t::flags

即使 spi_device_interface_config_t 结构体中的 spi_device_interface_config_t…
```

## 25. [test] 嵌入式开发 — 参考段 `S02#s21` + `S02#s28`

- 来源 S02；原文间隔 8；中文占比 A=0.524 B=0.565
- A 章节：SPI 主机驱动程序 > 使用驱动程序 > 在 SPI1 总线上使用 SPI 主机驱动程序的注意事项

```
备注

因具备 SPI 总线锁 特性，SPI 主机驱动程序可在 SPI1 总线上运行，但该过程十分棘手，且需要许多特殊处理。这是一个适合高级开发者的功能。

要在 SPI1 总线上运行 SPI 主机驱动程序，需注意以下两个问题：

当驱动在 SPI1 上运行时，代码和数据应在内部存储器中。

总线由设备、flash 中的数据（代码）缓存以及 PSRAM 共享。当其他驱动在 SPI1 总线上运行时，缓存应被禁用。此时，flash 上的数据（代码）以及 PSRAM 就不会在驱动获取 SPI 总线时被抓取。驱动可经由以下方式获取 SPI1 总线：

在 spi_device_acquire_bus() 和 spi_device_release_bus() 之间显式获取总线。

在 spi_device_polling_start() 和 spi_device_polling_end() 之间隐式获取总线（或在 spi_device_polling_transmit() 中）。

上述时间内，所有的其他任务和大多数 ISR 将被禁用（见 IRAM 安全中断处理程序）。当前任务所使用的应用程序代码和数据应放置于内存（DRAM 或 IRAM）中，或者已置于 ROM 中。访问外部存储器（flash 代码、flash 中的常量数据和 PSRAM 中的静态/堆积数据）将导致 缓存禁用情况下访问缓存内存区域 异常。关于 IRAM、DRAM、和 flash 缓存之间的区别，请参阅 应用程序内存布局。

要将函数置入 IRAM，请选择：

将 IRAM_ATTR （包括 esp_attr.h）添加到函数中，如：

IRAM_ATTR void foo(void) { }

函数内联时将跟随其调用者的段，且属性无法生效，使用 NOLINE_ATTR 可避免此类情况。注意，编译器可能会将部分代码转化为常量数据中的查找表，所以 noflash_text 并不安全。

或使用 linker.lf 中的 noflash 位置，请参阅 链接器脚本生成机制。注意，编译器可能会将部分代码转化为常量数据中的查找…
```
- B 章节：SPI 主机驱动程序 > 已知问题

```
写入和读取阶段同时进行时，半双工传输事务与 DMA 不兼容。

如需执行此类传输事务，可使用以下替代方案：

执行全双工传输事务。

将总线初始化函数的最后一个参数设置为 0 以禁用 DMA，即：

ret=spi_bus_initialize(SPI3_HOST, &buscfg, 0);

此举可避免传输和接收超过 64 字节的数据。
1. 尝试用命令和地址字段代替写入阶段。

全双工传输事务与 Dummy 位 不兼容，因此存在频率限制，请参阅 Dummy 位加速方案。

SPI 读取和写入阶段同时进行时，全双工模式和半双工模式下，spi_device_interface_config_t 和 spi_transaction_ext_t 中的 dummy_bits 均会失效。

全双工传输事务模式中，命令阶段和地址阶段与 cs_ena_pretrans 不兼容。

若启用了 DMA，则 RX 缓冲区应该以字对齐（从 32 位边界开始，字节长度为 4 的倍数）。否则 DMA 可能覆盖未对齐部分的数据。
```
