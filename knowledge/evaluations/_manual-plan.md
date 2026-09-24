# 待人工写题的段对清单

规则：每题 2 段、同来源、原文间隔 ≥ 3、不得使用已排除的段；
题面要像真实用户会问的问题，且不得出现与原文连续相同 12 字以上的片段。

## 01. [test] 嵌入式开发  —— 参考段 `S03#s61~2` + `S03#s61~10`

- 来源：S03；间隔：8
- A 章节：堆内存分配 > API 参考 - 堆分配 > Functions

```
ptr -- Pointer to previously allocated memory, or NULL for a new allocation.
size -- Size of the new buffer requested, or 0 to free the buffer.
caps -- Bitwise OR of MALLOC_CAP_* flags indicating the type of memory desired for the new allocation.
返回:
Pointer to a new buffer of size 'size' with capabilities 'caps', or NULL if allocation failed.
void *heap_caps_aligned_alloc(size_t alignment, size_t size, uint32_t caps)
Allocate an aligned chunk of memory which has the given capabilities.
Equivalent semantics to libc aligned_alloc(), for capability-aware memory.
参数:
alignment -- How the pointer received needs to be aligned must be a power of two
size -- Size, in bytes, of the amount of memory
```
- B 章节：堆内存分配 > API 参考 - 堆分配 > Functions

```
Function called to walk through the heaps with the given set of capabilities.
参数:
caps -- The set of capabilities assigned to the heaps to walk through
walker_func -- Callback called for each block of the heaps being traversed
user_data -- Opaque pointer to user defined data
void heap_caps_walk_all(heap_caps_walker_cb_t walker_func, void *user_data)
Function called to walk through all heaps defined by the heap component.
参数:
walker_func -- Callback called for each block of the heaps being traversed
user_data -- Opaque pointer to user defined data
```

## 02. [test] 嵌入式开发  —— 参考段 `S04#s78~3` + `S04#s78~10`

- 来源：S04；间隔：7
- A 章节：GPIO & RTC GPIO > API 参考 - 普通 GPIO > Functions

```
esp_err_t gpio_input_enable(gpio_num_t gpio_num)
Enable input for an IO.
参数:
gpio_num -- GPIO number
返回:
ESP_OK Success
ESP_ERR_INVALID_ARG GPIO number error
esp_err_t gpio_set_pull_mode(gpio_num_t gpio_num, gpio_pull_mode_t pull)
Configure GPIO internal pull-up/pull-down resistors.
备注
This function always overwrite the current pull-up/pull-down configurations
备注
ESP32: Only pins that support both input & output have integrated pull-up and pull-down resistors. Input-only GPIOs 34-39 do not.
参数:
gpio_num -- GPIO number. If you want to set pull up or down mode for e.g. GPIO16, gpio_num should be GPIO_NUM_16 (16);
pull -- GPIO pull up/down mode.
返回:
ESP_OK Success
ESP_ERR_INVALID_ARG : Parame
```
- B 章节：GPIO & RTC GPIO > API 参考 - 普通 GPIO > Functions

```
ESP_OK Success
ESP_ERR_INVALID_ARG GPIO error
esp_err_t gpio_sleep_set_pull_mode(gpio_num_t gpio_num, gpio_pull_mode_t pull)
Configure GPIO pull-up/pull-down resistors at sleep.
备注
ESP32: Only pins that support both input & output have integrated pull-up and pull-down resistors. Input-only GPIOs 34-39 do not.
参数:
gpio_num -- GPIO number. If you want to set pull up or down mode for e.g. GPIO16, gpio_num should be GPIO_NUM_16 (16);
pull -- GPIO pull up/down mode.
返回:
ESP_OK Success
ESP_ERR_INVALID_ARG : Parameter error
esp_err_t gpio_wakeup_enable_on_hp_periph_powerdown_sleep(gpio_num_t gpio_num, gpio_int_type_t intr_type)
Enable GPIO wake-up function on peripheral powerdowned sleep (includi
```

## 03. [test] 嵌入式开发  —— 参考段 `S03#s61~3` + `S03#s61~8`

- 来源：S03；间隔：5
- A 章节：堆内存分配 > API 参考 - 堆分配 > Functions

```
caps -- Bitwise OR of MALLOC_CAP_* flags indicating the type of memory to be returned
返回:
A pointer to the memory allocated on success, NULL on failure
void *heap_caps_calloc(size_t n, size_t size, uint32_t caps)
Allocate a chunk of memory which has the given capabilities. The initialized value in the memory is set to zero.
Equivalent semantics to libc calloc(), for capability-aware memory.
In IDF, calloc(p) is equivalent to heap_caps_calloc(p, MALLOC_CAP_8BIT).
参数:
n -- Number of continuing chunks of memory to allocate
size -- Size, in bytes, of a chunk of memory to allocate
caps -- Bitwise OR of MALLOC_CAP_* flags indicating the type of memory to be returned
返回:
A pointer to the memory al
```
- B 章节：堆内存分配 > API 参考 - 堆分配 > Functions

```
The variable parameters are bitwise OR of MALLOC_CAP_* flags indicating the type of memory. This API prefers to allocate memory with the first parameter. If failed, allocate memory with the next parameter. It will try in this order until allocating a chunk of memory successfully or fail to allocate memories with any of the parameters.
参数:
size -- Size, in bytes, of the amount of memory to allocate
num -- Number of variable parameters
返回:
A pointer to the memory allocated on success, NULL on failure
void *heap_caps_realloc_prefer(void *ptr, size_t size, size_t num, ...)
Reallocate a chunk of memory as preference in decreasing order.
参数:
ptr -- Pointer to previously allocated memory, or NULL 
```

## 04. [test] 测试自动化  —— 参考段 `S14#s235` + `S14#s276`

- 来源：S14；间隔：49
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
捕获，作为文本，输出到 sys.stdout 和 sys.stderr
```
- B 章节：Pytest夹具：显式、模块化、可扩展¶ > 覆盖不同级别的设备¶ > 用非参数化夹具替代参数化夹具，反之亦然。¶

```
假设测试文件结构为：
tests/
__init__.py
conftest.py
# content of tests/conftest.py
import pytest
@pytest.fixture(params=['one', 'two', 'three'])
def parametrized_username(request):
return request.param
@pytest.fixture
def non_parametrized_username(request):
return 'username'
test_something.py
# content of tests/test_something.py
import pytest
@pytest.fixture
def parametrized_username():
return 'overridden-username'
@pytest.fixture(params=['one', 'two', 'three'])
def non_parametrized_username(request):
return request.param
def test_username(parametrized_username):
assert parametrized_username == 'overridden-username'
def test_parametrized_username(non_parametrized_username):
assert non_parametrized_use
```

## 05. [test] 测试自动化  —— 参考段 `S14#s236` + `S14#s270~2`

- 来源：S14；间隔：42
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
- B 章节：Pytest夹具：显式、模块化、可扩展¶ > 按设备实例自动分组测试¶

```
test_module.py::test_2[mod1-2] SETUP otherarg 2
RUN test2 with otherarg 2 and modarg mod1
PASSED TEARDOWN otherarg 2
test_module.py::test_1[mod2] TEARDOWN modarg mod1
SETUP modarg mod2
RUN test1 with modarg mod2
PASSED
test_module.py::test_2[mod2-1] SETUP otherarg 1
RUN test2 with otherarg 1 and modarg mod2
PASSED TEARDOWN otherarg 1
test_module.py::test_2[mod2-2] SETUP otherarg 2
RUN test2 with otherarg 2 and modarg mod2
PASSED TEARDOWN otherarg 2
TEARDOWN modarg mod2
============================ 8 passed in 0.12s =============================
您可以看到参数化模块的作用域 modarg 资源导致测试执行的顺序，导致可能的“活动”资源最少。的终结器 mod1 参数化资源在 mod2 资源已设置。
特别要注意，测试_0是完全独立的，首先完成。然后使用 mod1 ，然后用测试 mod1 ，然后用 mod2 最后用 mod2 .
这个 othe
```

## 06. [test] 测试自动化  —— 参考段 `S13#s207` + `S13#s232`

- 来源：S13；间隔：40
- A 章节：How to use fixtures¶ > “Requesting” fixtures¶ > Fixtures can be requested more than once per test (return values are cached)¶

```
Fixtures can also be requested more than once during the same test, and
pytest won’t execute them again for that test. This means we can request
fixtures in multiple fixtures that are dependent on them (and even again in the
test itself) without those fixtures being executed more than once.
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
If a requested fixture was executed once for every time it was requested
```
- B 章节：How to use fixtures¶ > Overriding fixtures on various levels¶ > Override a parametrized fixture with non-parametrized one and vice versa¶

```
Given the tests file structure is:
tests/
conftest.py
# content of tests/conftest.py
import pytest
@pytest.fixture(params=['one', 'two', 'three'])
def parametrized_username(request):
return request.param
@pytest.fixture
def non_parametrized_username(request):
return 'username'
test_something.py
# content of tests/test_something.py
import pytest
@pytest.fixture
def parametrized_username():
return 'overridden-username'
@pytest.fixture(params=['one', 'two', 'three'])
def non_parametrized_username(request):
return request.param
def test_username(parametrized_username):
assert parametrized_username == 'overridden-username'
def test_parametrized_username(non_parametrized_username):
assert non_para
```

## 07. [test] 测试自动化  —— 参考段 `S14#s237` + `S14#s269`

- 来源：S14；间隔：39
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
测试也不必局限于单个装置。它们可以依赖于您想要
```
- B 章节：Pytest夹具：显式、模块化、可扩展¶ > 模块化：使用fixture函数中的fixture¶

```
除了在测试函数中使用fixture之外，fixture函数还可以使用其他fixture本身。这有助于夹具的模块化设计，并允许在许多项目中重复使用特定于框架的装置。作为一个简单的例子，我们可以扩展前面的例子并实例化一个对象 app 我们把已经定义好的 smtp_connection it资源：
# content of test_appsetup.py
import pytest
class App:
def __init__(self, smtp_connection):
self.smtp_connection = smtp_connection
@pytest.fixture(scope="module")
def app(smtp_connection):
return App(smtp_connection)
def test_smtp_connection_exists(app):
assert app.smtp_connection
我们在此声明 app 接收先前定义的 smtp_connection fixture并实例化 App 对象。让我们运行它：
$ pytest -v test_appsetup.py
=========================== test session starts ============================
platform linux -- Python 3.x.y, pytest-6.x.y, py-1.x.y, pluggy-0.x.y -- $PYTHON_PREFIX/bin/python
cache
```

## 08. [test] 测试自动化  —— 参考段 `S14#s238` + `S14#s268`

- 来源：S14；间隔：37
- A 章节：Pytest夹具：显式、模块化、可扩展¶ > “请求”装置¶

```
所以固定装置就是我们 准备 对于一个测试，但是我们如何告诉pytest哪些测试和装置需要哪些装置呢？
在基本级别上，测试函数通过将fixture声明为参数来请求fixture，如 test_my_fruit_in_basket(my_fruit, fruit_basket): 在前面的示例中。
在基本级别上，pytest依赖于一个测试来告诉它它需要什么装置，所以我们必须将该信息构建到测试本身中。我们必须进行测试。“ 请求 它所依赖的装置，要做到这一点，我们必须将这些装置作为参数列在测试函数的“签名”中(这是 def test_something(blah, stuff, more): 线）。
当pytest运行测试时，它会查看该测试函数签名中的参数，然后搜索与这些参数同名的fixture。一旦pytest找到它们，它就会运行这些装置，捕获它们返回的内容(如果有的话)，并将这些对象作为参数传递给测试函数。
```
- B 章节：Pytest夹具：显式、模块化、可扩展¶ > 对参数化夹具使用标记¶

```
pytest.param() 可用于在参数化装置的值集中应用标记，方法与它们可用于的方法相同 @pytest.mark.parametrize .
例子：
# content of test_fixture_marks.py
import pytest
@pytest.fixture(params=[0, 1, pytest.param(2, marks=pytest.mark.skip)])
def data_set(request):
return request.param
def test_data(data_set):
pass
运行此测试将 skip 调用 data_set 有价值 2 ：
$ pytest test_fixture_marks.py -v
=========================== test session starts ============================
platform linux -- Python 3.x.y, pytest-6.x.y, py-1.x.y, pluggy-0.x.y -- $PYTHON_PREFIX/bin/python
cachedir: $PYTHON_PREFIX/.pytest_cache
rootdir: $REGENDOC_TMPDIR
collecting ... collected 3 items
test_fixture_marks.py::test_data[0] PASSED [ 33%]
test_fixture_marks.py::test_data[1] PA
```

## 09. [test] 测试自动化  —— 参考段 `S14#s243` + `S14#s267~2`

- 来源：S14；间隔：30
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
- B 章节：Pytest夹具：显式、模块化、可扩展¶ > 参数化夹具¶

```
def test_ehlo(smtp_connection):
response, msg = smtp_connection.ehlo()
assert response == 250
> assert b"smtp.gmail.com" in msg
E AssertionError: assert b'smtp.gmail.com' in b'mail.python.org\nPIPELINING\nSIZE 51200000\nETRN\nSTARTTLS\nAUTH DIGEST-MD5 NTLM CRAM-MD5\nENHANCEDSTATUSCODES\n8BITMIME\nDSN\nSMTPUTF8\nCHUNKING'
test_module.py:6: AssertionError
finalizing <smtplib.SMTP object at 0xdeadbeef>
________________________ test_noop[mail.python.org] ________________________
smtp_connection = <smtplib.SMTP object at 0xdeadbeef>
def test_noop(smtp_connection):
response, msg = smtp_connection.noop()
assert response == 250
> assert 0 # for demo purposes
E assert 0
test_module.py:13: AssertionEr
```

## 10. [test] 测试自动化  —— 参考段 `S13#s209~2` + `S13#s225~2`

- 来源：S13；间隔：28
- A 章节：How to use fixtures¶ > Scope: sharing fixtures across classes, modules, packages or session¶

```
$ pytest test_module.py
=========================== test session starts ============================
platform linux -- Python 3.x.y, pytest-9.x.y, pluggy-1.x.y
rootdir: /home/sweet/project
collected 2 items
test_module.py FF [100%]
================================= FAILURES =================================
________________________________ test_ehlo _________________________________
smtp_connection = <smtplib.SMTP object at 0xdeadbeef0001>
def test_ehlo(smtp_connection):
response, msg = smtp_connection.ehlo()
assert response == 250
assert b"smtp.gmail.com" in msg
> assert 0 # for demo purposes
^^^^^^^^
E assert 0
test_module.py:7: AssertionError
________________________________ test_noop ___
```
- B 章节：How to use fixtures¶ > Modularity: using fixtures from a fixture function¶

```
Due to the parametrization of smtp_connection, the test will run twice with two
different App instances and respective smtp servers. There is no
need for the app fixture to be aware of the smtp_connection
parametrization because pytest will fully analyse the fixture dependency graph.
Note that the app fixture has a scope of module and uses a
module-scoped smtp_connection fixture. The example would still work if
smtp_connection was cached on a session scope: it is fine for fixtures to use
“broader” scoped fixtures but not the other way round:
A session-scoped fixture could not use a module-scoped one in a
meaningful way.
```

## 11. [test] AI框架与推理  —— 参考段 `S05#s110` + `S05#s110~6`

- 来源：S05；间隔：5
- A 章节：端侧推理快速入门 > Windows篇 > 模型推理 > 集成推理

```
在上一节，我们使用了官方推理测试工具进行了模型推理测试，在本节，我们将以使用MindSpore Lite的C++接口进行集成为例，演示如何使用MindSpore Lite的发布件，进行集成开发，编写自己的推理程序。
环境要求
系统环境：Windows 7，Windows 10；64位。
MinGW 编译依赖
CMake >= 3.22.3
编译64位：MinGW-W64 x86_64 = GCC-7.3.0
编译32位：MinGW-W64 i686 = GCC-7.3.0
得到版本发布件
用户可通过MindSpore Lite官网，获得MindSpore Lite发布件，点击此处查看各版本。
在本例中，仍然采用了和前几节一样的发布件，用于本节集成开发，点击此处可直接下载。
在本节简单的推理集成例子中，需要用到的发布件内容如下：
mindspore-lite-{version}-win-x64
└── runtime
├── include
└── lib
├── libgcc_s_seh-1.dll # MinGW动态库
├── libmindspore-lite.a # MindSpore Lite推理框架的静态库
├── libmindspore-lite.dll # MindSpore Lite推理框架的动态库
├── libmindspore-lite.dll.a # MindSpore Lite推理框架的动态库的链接文件
├── libssp-0.dll # MinGW动态库
├── libstdc++-6.dll # MinGW动态库
└── libwinpthre
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
进入build目录，输入cmake -G "CodeBlocks - MinGW Makefiles" ..生成makefile文件，然后输入cmake --build .编译工程。在编译成功后，可以在build目录下得到
```

## 12. [test] AI框架与推理  —— 参考段 `S05#s95` + `S05#s110~5`

- 来源：S05；间隔：39
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
└── 
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
在
```

## 13. [test] AI框架与推理  —— 参考段 `S05#s97~2` + `S05#s110~4`

- 来源：S05；间隔：34
- A 章节：端侧推理快速入门 > Linux篇 > 模型转换 > Netron可视化

```
mobilenetv2.ms模型的理解。
通过对模型的查看，可以知道mobilenetv2.ms模型定义了如下计算：
对格式为float32[1,224,224,3]的输入张量x进行不断卷积，最后通过MatMulFusion全连接层的矩阵乘法操作，并执行Softmax运算，得到1x1000的输出张量，该输出张量名为Default/head-MobileNetV2Head/Softmax-op204。
本例提供的mobilenetv2.ms模型为1000分类的图片分类模型，具体的分类类别本处不做叙述，但通过对模型的查看，可以知道该模型不包含对图片的前处理操作，接收1x224x224x3的float32数值，并得到1x1000的float32输出。
故在使用该模型进行推理时，用户需自行编码完成图片的前处理操作，将处理后的数据，传递给推理框架，进行前向推理，并对推理得到的1x1000的输出进行后处理。
```
- B 章节：端侧推理快速入门 > Windows篇 > 模型推理 > 集成推理

```
// Get Input
auto inputs = model->GetInputs();
for (auto tensor : inputs) {
auto input_data = reinterpret_cast<float *>(tensor.MutableData());
if (input_data == nullptr) {
std::cerr << "MallocData for inTensor failed." << std::endl;
delete model;
return -1;
}
GenerateRandomData<float>(tensor.DataSize(), input_data, std::uniform_real_distribution<float>(0.1f, 1.0f));
}
// Predict
std::vector<MSTensor> outputs;
auto status = model->Predict(inputs, &outputs);
if (status != mindspore::kSuccess) {
std::cerr << "Inference error." << std::endl;
delete model;
return -1;
}
// Get Output Tensor Data.
std::cout << "\n------- print outputs ----------" << std::endl;
for (auto tensor : outputs) {
std::cou
```

## 14. [test] AI框架与推理  —— 参考段 `S05#s101~4` + `S05#s109~4`

- 来源：S05；间隔：20
- A 章节：端侧推理快速入门 > Linux篇 > 模型推理 > 集成推理

```
// Predict
std::vector<MSTensor> outputs;
auto status = model->Predict(inputs, &outputs);
if (status != mindspore::kSuccess) {
std::cerr << "Inference error." << std::endl;
delete model;
return -1;
}
// Get Output Tensor Data.
std::cout << "\n------- print outputs ----------" << std::endl;
for (auto tensor : outputs) {
std::cout << "out tensor name is:" << tensor.Name() << "\nout tensor size is:" << tensor.DataSize()
<< "\nout tensor elements num is:" << tensor.ElementNum() << std::endl;
auto out_data = reinterpret_cast<float *>(tensor.MutableData());
std::cout << "output data is:";
for (int i = 0; i < tensor.ElementNum(); i++) {
std::cout << out_data[i] << " ";
}
std::cout << std::endl;
}
s
```
- B 章节：端侧推理快速入门 > Windows篇 > 模型推理 > benchmark推理测试

```
在输出信息中，InData 0行打印的是该次推理的输入数据（只打印了前20个），Data of node Default/head-MobileNetV2Head/Softmax-op204行打印的是相关输出节点（Default/head-MobileNetV2Head/Softmax-op204）的推理结果（只打印前50个值），可以直接观察它们跟标杆文件的差异，以获得直观感受。
Mean bias of node/tensor Default/head-MobileNetV2Head/Softmax-op204行，给出了Default/head-MobileNetV2Head/Softmax-op204输出张量与标杆数据对比的平均误差，该误差计算方法为benchmark工具自带的比较算法。
在最后，Mean bias of all nodes/tensors给出了所有张量与标杆对比的平均误差，在本例中，只有1个输出张量，故总平均误差和Default/head-MobileNetV2Head/Softmax-op204张量误差一致。可以观察到，推理的总平均误差为0%。
高级功能
关于benchmark的更详细说明，以及关于如何使用benchmark来进行基准测试、耗时定量分析、误差分析、Dump数据等，可以参考benchmark。
表4：benchmark参数定义
参数名
是否必选
参数说明
参数类型
默认值
--modelFile=<MODELPATH>
必选
指定需要进行基准测试的MindSpore Lite模型文件路径。
String
null
--numThreads=
```

## 15. [test] AI框架与推理  —— 参考段 `S07#2.3` + `S07#s142`

- 来源：S07；间隔：17
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
相对应地，飞桨也支持将 Te
```

## 16. [test] 职业能力  —— 参考段 `S16#s298` + `S16#s298~6`

- 来源：S16；间隔：5
- A 章节：Software Quality Assurance Analysts and Testers > Software Skills

```
Access software — Citrix cloud computing software; PuTTY
Accounting software — Tax software
Administration software — Software distribution management software
Analytical or scientific software — IBM SPSS Statistics Hot Technology ; Minitab; SAS Hot Technology ; The MathWorks MATLAB Hot Technology ; 5 more
Application server software — Atlassian Bitbucket Hot Technology ; GitLab Hot Technology ; Red Hat OpenShift Hot Technology ; Spring Boot Hot Technology ; 6 more
Backup or archival software — Backup and archival software; Veritas NetBackup
Business intelligence and data analysis software — IBM Cognos Impromptu; Oracle Business Intelligence Enterprise Edition; Qlik Tech QlikView; Tableau Ho
```
- B 章节：Software Quality Assurance Analysts and Testers > Software Skills

```
Transaction security and virus protection software — Anti-spyware software; Antivirus software; McAfee; NortonLifeLock cybersecurity software; 1 more
Transaction server software — Customer information control system CICS; IBM Middleware; Microsoft Internet Information Services (IIS); Object Management Group Object Request Broker; 1 more
Video conferencing software — Cisco Webex Hot Technology ; Google Meet; LogMeIn GoToMeeting; Zoom Hot Technology ; 1 more
Video creation and editing software — Adobe After Effects Hot Technology ; Flipgrid; Screencastify; YouTube; 1 more
Web page creation and editing software — Adobe Dreamweaver; Google Sites; LinkedIn; Social media sites
Web platform develop
```

## 17. [test] 职业能力  —— 参考段 `S16#s298~2` + `S16#s298~5`

- 来源：S16；间隔：3
- A 章节：Software Quality Assurance Analysts and Testers > Software Skills

```
Configuration management software — Chef Hot Technology ; Perforce Helix software; Puppet Hot Technology ; Visible Razor; 6 more
Content workflow software — Emerald Software Group Emerald Green Office; Twiki; Workflow software
Customer relationship management CRM software — Blackbaud The Raiser's Edge; Oracle Eloqua; Salesforce software Hot Technology
Data base management system software — Amazon DynamoDB Hot Technology ; Elasticsearch Hot Technology ; MongoDB Hot Technology ; Oracle PL/SQL Hot Technology ; 22 more
Data base reporting software — Microsoft SQL Server Reporting Services SSRS Hot Technology ; Oracle Business Intelligence Discoverer; SAP Business Intelligence; SAP Crystal Report
```
- B 章节：Software Quality Assurance Analysts and Testers > Software Skills

```
Object oriented data base management software — Hibernate ORM Hot Technology ; PostgreSQL Hot Technology
Office suite software — LibreOffice; Microsoft Office software
Operating system software — Google Android Hot Technology ; Microsoft Windows Server Hot Technology ; Red Hat Enterprise Linux Hot Technology ; UNIX Shell Hot Technology ; 23 more
Platform interconnectivity software — Migration software
Portal server software — Apache HTTP Server
Presentation software — Google Slides; Microsoft PowerPoint Hot Technology
Process mapping and design software — Microsoft Visio Hot Technology
Program testing software — Hewlett Packard LoadRunner; IBM Rational Robot; JUnit Hot Technology ; Selenium 
```

## 18. [test] 职业能力  —— 参考段 `S16#s292~2` + `S16#s305`

- 来源：S16；间隔：23
- A 章节：Software Quality Assurance Analysts and Testers > Work Activities

```
Organizing, Planning, and Prioritizing Work — Developing specific goals and plans to prioritize, organize, and accomplish your work.
Establishing and Maintaining Interpersonal Relationships — Developing constructive and cooperative working relationships with others, and maintaining them over time.
Making Decisions and Solving Problems — Analyzing information and evaluating results to choose the best solution and solve problems.
Thinking Creatively — Developing, designing, or creating new applications, ideas, relationships, systems, or products, including artistic contributions.
Monitoring Processes, Materials, or Surroundings — Monitoring and reviewing information from materials, events, or 
```
- B 章节：Software Quality Assurance Analysts and Testers > Work Styles

```
Intellectual Curiosity — A tendency to seek out and acquire new work-related knowledge and obtain a deep understanding of work-related subjects.
Cautiousness — A tendency to be careful, deliberate, and risk-avoidant when making work-related decisions or doing work.
Attention to Detail — A tendency to be detail-oriented, organized, and thorough in completing work.
Dependability — A tendency to be reliable, responsible, and consistent in meeting work-related obligations.
back to top
```

## 19. [test] 职业能力  —— 参考段 `S16#s292~3` + `S16#s304~2`

- 来源：S16；间隔：21
- A 章节：Software Quality Assurance Analysts and Testers > Work Activities

```
Coaching and Developing Others — Identifying the developmental needs of others and coaching, mentoring, or otherwise helping others to improve their knowledge or skills.
Developing Objectives and Strategies — Establishing long-range objectives and specifying the strategies and actions to achieve them.
Providing Consultation and Advice to Others — Providing guidance and expert advice to management or other groups on technical, systems-, or process-related topics.
Training and Teaching Others — Identifying the educational needs of others, developing formal educational or training programs or classes, and teaching or instructing others.
back to top
```
- B 章节：Software Quality Assurance Analysts and Testers > Interests

```
Realistic — Work involves designing, building, or repairing of equipment, materials, or structures, engaging in physical activity, or working outdoors. Realistic occupations are often associated with engineering, mechanics and electronics, construction, woodworking, transportation, machine operation, agriculture, animal services, physical or manual labor, athletics, or protective services.
Engineering — Work involves applying science and technology to the design, building, testing, and use of electrical and electronic components, mechanical devices and machines, automotive, marine, and aerospace equipment and vehicles, materials, or structures.
back to top
```

## 20. [test] 计算机视觉  —— 参考段 `S10#s167` + `S10#s171`

- 来源：S10；间隔：5
- A 章节：使用 Ultralytics YOLO 进行实例分割# > Predict#

```
使用训练好的 YOLO26n-seg 模型对图像运行预测。
示例
from ultralytics import YOLO
# Load a model
model = YOLO("yolo26n-seg.pt") # load an official model
model = YOLO("path/to/best.pt") # load a custom model
# Predict with the model
results = model("https://ultralytics.com/images/bus.jpg") # predict on an image
# Access the results
for result in results:
xy = result.masks.xy # mask polygons in pixel coordinates
xyn = result.masks.xyn # normalized mask polygons
masks = result.masks.data # binary masks, shape (N,H,W), dtype torch.uint8
请在预测页面查看完整的 predict 模式详情。
```
- B 章节：使用 Ultralytics YOLO 进行实例分割# > 常见问题#

```
要在自定义数据集上训练 YOLO26 分割模型，你需要先准备好 YOLO 分割格式的数据集。你可以使用内置的 convert_coco 工具来转换 COCO JSON 数据集。数据集准备好后，你可以使用 Python 或 CLI 命令来训练模型：
示例
from ultralytics import YOLO
# Load a pretrained YOLO26 segment model
model = YOLO("yolo26n-seg.pt")
# Train the model
results = model.train(data="path/to/your_dataset.yaml", epochs=100, imgsz=640)
请查看配置页面，了解更多可用参数。
目标检测通过在对象周围绘制边界框来识别和定位图像中的对象，而实例分割不仅能识别边界框，还能勾勒出每个对象的确切形状。YOLO26 实例分割模型会提供用于勾勒每个检测对象的掩码或轮廓，这对于需要了解对象精确形状的任务尤其有用，例如医学影像或自动驾驶。
Ultralytics YOLO26 是一种最先进的模型，以高准确率和实时性能著称，非常适合实例分割任务。YOLO26 Segment 模型在 COCO 数据集上进行预训练，确保在各种对象上的稳健性能。此外，YOLO 支持训练、验证、预测和导出功能，并能实现无缝集成，因此在研究和行业应用中都具有很高的灵活性。
加载和验证预训练的 YOLO 分割模型非常简单。下面介绍如何使用 Python 和 CLI 完成此操作：
示例
from ultralytics imp
```

## 21. [dev] 测试自动化  —— 参考段 `S14#s244` + `S14#s267`

- 来源：S14；间隔：28
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
- B 章节：Pytest夹具：显式、模块化、可扩展¶ > 参数化夹具¶

```
fixture函数可以参数化，在这种情况下，它们将被多次调用，每次执行一组相关的测试，即依赖于该fixture的测试。测试函数通常不需要知道它们的重新运行。夹具参数化有助于为组件编写详尽的功能测试，这些组件本身可以通过多种方式进行配置。
扩展前面的示例，我们可以标记fixture以创建两个 smtp_connection fixture实例，它将导致使用fixture的所有测试运行两次。fixture函数通过 request 对象：
# content of conftest.py
import pytest
import smtplib
@pytest.fixture(scope="module", params=["smtp.gmail.com", "mail.python.org"])
def smtp_connection(request):
smtp_connection = smtplib.SMTP(request.param, 587, timeout=5)
yield smtp_connection
print("finalizing {}".format(smtp_connection))
smtp_connection.close()
主要变化是 params 具有 @pytest.fixture ，fixture函数将执行的每个值的列表，可以通过 request.param . 无需更改测试功能代码。让我们再跑一次：
$ pytest -q test_module.py
FFFF [100%]
===============================
```

## 22. [dev] 测试自动化  —— 参考段 `S12#s182` + `S12#s197`

- 来源：S12；间隔：26
- A 章节：等待策略 > 隐式等待 > /examples/java/src/test/java/dev/selenium/waits/WaitsTest.java

```
package dev.selenium.waits;
import dev.selenium.BaseTest;
import java.time.Duration;
import org.junit.jupiter.api.Assertions;
import org.junit.jupiter.api.Test;
import org.openqa.selenium.By;
import org.openqa.selenium.ElementNotInteractableException;
import org.openqa.selenium.NoSuchElementException;
import org.openqa.selenium.WebDriver;
import org.openqa.selenium.WebElement;
import org.openqa.selenium.chrome.ChromeOptions;
import org.openqa.selenium.support.ui.FluentWait;
import org.openqa.selenium.support.ui.Wait;
import org.openqa.selenium.support.ui.WebDriverWait;
public class WaitsTest extends BaseTest {
@Test
public void fails() {
startChromeDriver(new ChromeOptions());
driver.get("ht
```
- B 章节：等待策略 > 显式等待 > 定制 > /examples/ruby/spec/waits/waits_spec.rb

```
# frozen_string_literal: true
require 'spec_helper'
RSpec.describe 'Waits' do
let(:driver) { start_session }
it 'fails' do
driver.get 'https://www.selenium.dev/selenium/web/dynamic.html'
driver.find_element(id: 'adder').click
expect {
driver.find_element(id: 'box0')
}.to raise_error(Selenium::WebDriver::Error::NoSuchElementError)
end
it 'sleeps' do
driver.get 'https://www.selenium.dev/selenium/web/dynamic.html'
driver.find_element(id: 'adder').click
sleep 1
added = driver.find_element(id: 'box0')
expect(added.dom_attribute(:class)).to eq('redbox')
end
it 'implicit' do
driver.manage.timeouts.implicit_wait = 2
driver.get 'https://www.selenium.dev/selenium/web/dynamic.html'
driver.find_element(
```

## 23. [dev] 测试自动化  —— 参考段 `S14#s245` + `S14#s266`

- 来源：S14；间隔：26
- A 章节：Pytest夹具：显式、模块化、可扩展¶ > 范围：跨类、模块、包或会话共享fixture¶

```
需要网络访问的设备依赖于连接性，通常创建成本很高。扩展前面的示例，我们可以添加 scope="module" 参数 @pytest.fixture 调用以导致 smtp_connection Fixture函数，负责创建与先前存在的SMTP服务器的连接，每次测试仅调用一次 模块 （默认情况下，每个测试调用一次 功能 ）因此，一个测试模块中的多个测试功能将接收相同的 smtp_connection 夹具实例，节省时间。的可能值 scope 是： function ， class ， module ， package 或 session .
下一个示例将fixture函数放入单独的 conftest.py fixture可以从目录中的多个测试模块访问以下功能：
# content of conftest.py
import pytest
import smtplib
@pytest.fixture(scope="module")
def smtp_connection():
return smtplib.SMTP("smtp.gmail.com", 587, timeout=5)
# content of test_module.py
def test_ehlo(smtp_connection):
response, msg = smtp_connection.ehlo()
assert response == 250
assert b"smtp.gmail.com" in msg
assert 0 # for demo purposes
def test_noop(smtp_co
```
- B 章节：Pytest夹具：显式、模块化、可扩展¶ > 工厂作为固定装置¶

```
“工厂作为夹具”模式有助于在单个测试中多次需要夹具结果的情况下。夹具不直接返回数据，而是返回一个生成数据的函数。然后可以在测试中多次调用此函数。
工厂可以根据需要设置参数：
@pytest.fixture
def make_customer_record():
def _make_customer_record(name):
return {"name": name, "orders": []}
return _make_customer_record
def test_customer_records(make_customer_record):
customer_1 = make_customer_record("Lisa")
customer_2 = make_customer_record("Mike")
customer_3 = make_customer_record("Meredith")
如果工厂创建的数据需要管理，设备可以处理：
@pytest.fixture
def make_customer_record():
created_records = []
def _make_customer_record(name):
record = models.Customer(name=name, orders=[])
created_records.append(record)
return record
yield _make_customer_record
for record in created_records:
record.destroy()
def 
```

## 24. [dev] AI框架与推理  —— 参考段 `S05#s101~5` + `S05#s109~2`

- 来源：S05；间隔：17
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
auto input_data = reinterpret_cast<float *>(tensor.MutableData())
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
[输出节点2名称] [输出节点2形状的维度长度] [输出节点2形状的第一维值] ... [输出节点2形状
```

## 25. [dev] 职业能力  —— 参考段 `S17#s321~2` + `S17#s334`

- 来源：S17；间隔：19
- A 章节：Computer Hardware Engineers > Work Activities

```
Estimating the Quantifiable Characteristics of Products, Events, or Information — Estimating sizes, distances, and quantities; or determining time, costs, resources, or materials needed to perform a work activity.
Documenting/Recording Information — Entering, transcribing, recording, storing, or maintaining information in written or electronic/magnetic form.
Identifying Objects, Actions, and Events — Identifying information by categorizing, estimating, recognizing differences or similarities, and detecting changes in circumstances or events.
Organizing, Planning, and Prioritizing Work — Developing specific goals and plans to prioritize, organize, and accomplish your work.
Drafting, Laying Ou
```
- B 章节：Computer Hardware Engineers > Work Styles

```
Innovation — A tendency to be inventive, to be imaginative, and to adopt new perspectives on ways to accomplish work.
Adaptability — A tendency to be open to and comfortable with change, new experiences, or ideas at work.
Achievement Orientation — A tendency to establish and maintain personally challenging work-related goals, set high work-related standards, and exert high effort toward meeting those goals and standards.
Intellectual Curiosity — A tendency to seek out and acquire new work-related knowledge and obtain a deep understanding of work-related subjects.
Attention to Detail — A tendency to be detail-oriented, organized, and thorough in completing work.
Dependability — A tendency to 
```
