import os
import shutil


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def generate_docs():
    build_dir = os.path.join(repo_root, "docs", "build")
    if os.path.isdir(build_dir):
        shutil.rmtree(build_dir)
    os.mkdir(build_dir)

    # open the .rst file
    fname = os.path.join(repo_root, "docs", "build", "api.rst")
    with open(fname, 'w') as api_docs:

        # add header
        api_docs.write('Malcolm API\n===========\n\n')
        malcolm_root = os.path.join(repo_root, 'malcolm')

        # add the core docs
        core_root = os.path.join(malcolm_root, 'core')
        section = "malcolm.core"
        filenames = filter_filenames(core_root)
        doc_dir = os.path.join("..", "developer_docs", "core_api")
        add_module_entry(api_docs, section, doc_dir, filenames)

        # create entries in the .rst file for each module
        modules_root = os.path.join(malcolm_root, 'modules')
        for modulename in sorted(os.listdir(modules_root)):
            module_root = os.path.join(modules_root, modulename)
            if not os.path.isdir(module_root):
                continue
            # Copy the docs dir if it exists
            docs_build = os.path.join(repo_root, "docs", "build", modulename)
            docs_dir = os.path.join(module_root, "docs")
            if os.path.isdir(docs_dir):
                shutil.copytree(docs_dir, docs_build)
            for dirname in sorted(os.listdir(module_root)):
                if dirname in ["controllers", "parts", "infos", "vmetas"]:
                    # Only document places we know python files will live
                    section = "malcolm.modules.%s.%s" % (modulename, dirname)
                    filenames = filter_filenames(os.path.join(
                        module_root, dirname))
                    add_module_entry(api_docs, section, modulename, filenames)
                elif dirname == "parameters.py":
                    # TODO: Also document parameters
                    pass
        add_indices_and_tables(api_docs)


def filter_filenames(root):
    filenames = [f for f in sorted(os.listdir(root))
                 if f.endswith(".py") and f != "__init__.py"]
    return filenames


def add_module_entry(api_docs, section, doc_dir, filenames):
    api_docs.write(section + '\n' + '-' * len(section) + '\n')
    api_docs.write('\n..  toctree::\n')
    for filename in filenames:
        api_docs.write(' ' * 4 + os.path.join(doc_dir, filename[:-3]) + '\n')
    api_docs.write("\n")


def add_indices_and_tables(f):
    f.write('Indices and tables\n')
    f.write('==================\n')
    f.write('* :ref:`genindex`\n')
    f.write('* :ref:`modindex`\n')
    f.write('* :ref:`search`\n')


if __name__ == "__main__":
    generate_docs()
