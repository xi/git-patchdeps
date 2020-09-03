# git-patchdeps

Tool for analyzing dependencies among git commits.

Given a set of commits, `git-patchdeps` can find out that a specific
commit modifies a line introduced by an earlier commit, and mark these
commits as dependent. This can help you to determine which commits can
be reordered without problems.

Note that this tool can only detect textual dependencies. For logical
dependencies, where a patches applies just fine without another patch,
but does need the other to actually work, you'll still have to think
yourself :-)

Example:

    $ git patchdeps 6643668..c496aed
    7e69236 staging: dwc2: use dwc2_hcd_get_frame_number where possible
    22cbead staging: dwc2: add helper variable to simplify code                          X
    b4f0a76 staging: dwc2: unshift non-bool register value constants --------------------'     X           X
    e6bd5db staging: dwc2: only read the snpsid register once                                  |           X
    9637daf staging: dwc2: remove some device-mode related debug code                          |           |
    26f69bb staging: dwc2: simplify register shift expressions --------------------------------'           |
    9e06814 staging: dwc2: add missing shift                                                               |
    9e3b1d5 staging: dwc2: simplify debug output in dwc_hc_init                                            |
    e840c95 staging: dwc2: re-use hptxfsiz variable                                                    X X |
    420b4ba staging: dwc2: remove redundant register reads --------------------------------------------' X |
    70f5d3f staging: dwc2: properly mask the GRXFSIZ register -------------------------------------------' X
    aad6d16 staging: dwc2: interpret all hwcfg and related register at init time --------------------------' X   X
    51f794e staging: dwc2: validate the value for phy_utmi_width --------------------------------------------'   |
    5cc9513 staging: dwc2: make dwc2_core_params documentation more complete                                     |
    f7bafa7 staging: dwc2: add const to handling of dwc2_core_params --------------------------------------------'
    730b35b staging: dwc2: Remove some useless debug prints
    c496aed staging: dwc2: cleanup includes in pci.c
